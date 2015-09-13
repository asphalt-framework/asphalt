from typing import Optional, Callable, Any, Union, Iterable, Tuple
from asyncio import get_event_loop, coroutine, iscoroutinefunction
from collections import defaultdict
import asyncio
import time

from .util import qualified_name, asynchronous
from .event import EventSource, Event

__all__ = 'ResourceEvent', 'ResourceConflict', 'ResourceNotFound', 'Context'


class Resource:
    __slots__ = 'value', 'types', 'alias', 'context_var', 'creator'

    def __init__(self, value, types: tuple, alias: str, context_var: Optional[str],
                 creator: Callable[['Context'], Any]=None):
        self.value = value
        self.types = types
        self.alias = alias
        self.context_var = context_var
        self.creator = creator

    def get_value(self, ctx: 'Context'):
        if self.value is None and self.creator is not None:
            self.value = self.creator(ctx)
            if self.context_var:
                setattr(ctx, self.context_var, self.value)

        return self.value

    def __repr__(self):
        return '{0.__class__.__name__}({0})'.format(self)

    def __str__(self):
        return ('types={0.types!r}, alias={0.alias!r}, value={0.value!r}, '
                'context_var={0.context_var!r}, lazy={1}'.format(self, self.creator is not None))


class ResourceEvent(Event):
    """
    Dispatched when a resource has been added or removed to a context.

    :ivar source: the relevant context
    :ivar types: names of the types for the resource
    :ivar alias: the alias of the resource
    :ivar lazy: ``True`` if this is a lazily created resource, ``False`` if not
    """

    __slots__ = 'types', 'alias', 'lazy'

    def __init__(self, source: 'Context', topic: str, types: Tuple[str], alias: str,
                 lazy: bool):
        super().__init__(source, topic)
        self.types = types
        self.alias = alias
        self.lazy = lazy


class ResourceConflict(Exception):
    """
    Raised when a new resource that is being added conflicts with an existing resource or context
    variable.
    """


class ResourceNotFound(LookupError):
    """Raised when a resource request cannot be fulfilled within the allotted time."""

    def __init__(self, type: str, alias: str):
        super().__init__(type, alias)
        self.type = type
        self.alias = alias

    def __str__(self):
        return 'no matching resource was found for type={0.type!r} alias={0.alias!r}'.format(self)


class Context(EventSource):
    """
    Contexts give request handlers and callbacks access to resources.

    Contexts are stacked in a way that accessing an attribute that is not present in the current
    context causes the attribute to be looked up in the parent instance and so on, until the
    attribute is found (or ``AttributeError`` is raised).

    Supported events:

      * started (:class:`~asphalt.core.event.Event`): the context has been activated
      * finished (:class:`~asphalt.core.event.Event`): the context has served its purpose and is \
        being discarded
      * resource_added (:class:`ResourceEvent`): a resource has been added to this context
      * resource_removed (:class:`ResourceEvent`): a resource has been removed from this context

    :ivar Exception exception: the exception that occurred before exiting this context
                               (available to finish callbacks)
    """

    exception = None  # type: BaseException

    def __init__(self, parent: 'Context'=None):
        super().__init__()
        self._register_topics({
            'started': Event,
            'finished': Event,
            'resource_added': ResourceEvent,
            'resource_removed': ResourceEvent
        })
        self._parent = parent
        self._resources = defaultdict(dict)  # type: Dict[str, Dict[str, Resource]]
        self._resource_creators = {}  # type: Dict[str, Callable[[Context], Any]

        # Forward resource events from the parent(s)
        if parent is not None:
            parent.add_listener('resource_added', self._dispatch)
            parent.add_listener('resource_removed', self._dispatch)

    def __getattr__(self, name):
        creator = self._resource_creators.get(name)
        if creator is not None:
            value = creator(self)
            setattr(self, name, value)
            return value

        if self._parent is not None:
            return getattr(self._parent, name)

        raise AttributeError('no such context variable: {}'.format(name))

    def __enter__(self):
        get_event_loop().run_until_complete(self.dispatch('started'))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exception = exc_val
        get_event_loop().run_until_complete(self.dispatch('finished'))

    @coroutine
    def __aenter__(self):
        yield from self.dispatch('started')
        return self

    @coroutine
    def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exception = exc_val
        yield from self.dispatch('finished')

    def _add_resource(self, value, alias: str, context_var: str,
                      types: Union[Union[str, type], Iterable[Union[str, type]]],
                      creator: Optional[Callable[['Context'], Any]]):
        assert isinstance(alias, str) and alias, 'alias must be a nonempty string'
        assert context_var is None or isinstance(context_var, str),\
            'context_var must be a nonempty string or None'

        if not types and value is not None:
            types = (qualified_name(type(value)),)
        else:
            types = (types,) if isinstance(types, (str, type)) else types
            types = tuple(t if isinstance(t, str) else qualified_name(t) for t in types)

        # Check for name conflicts with existing resources
        for typename in types:
            conflicting = self._resources[typename].get(alias)
            if conflicting is not None:
                raise ResourceConflict('"{}" conflicts with {!r}'.format(alias, conflicting))

        resource = Resource(value, types, alias, context_var, creator)
        if resource.context_var:
            # Check that there is no existing attribute by that name
            if resource.context_var in dir(self):
                raise ResourceConflict(
                    '{!r} conflicts with an existing context attribute'.format(resource))

            # Check that there is no existing resource creator by that name
            if resource.context_var in self._resource_creators:
                raise ResourceConflict(
                    '{!r} conflicts with an existing lazy resource'.format(resource))

        # Register the resource
        for typename in types:
            self._resources[typename][resource.alias] = resource

        if creator is not None and context_var is not None:
            self._resource_creators[context_var] = creator

        # Add the resource as an attribute of this context if context_var is defined
        if creator is None and resource.context_var:
            setattr(self, context_var, value)

        self.dispatch('resource_added', types, alias, False)
        return resource

    @asynchronous
    def add_resource(
            self, value, alias: str='default', context_var: str=None, *,
            types: Union[Union[str, type], Iterable[Union[str, type]]]=()) -> Resource:
        """
        Adds a resource to the collection and dispatches a "resource_added" event.

        :param value: the actual resource value
        :param alias: an identifier for this resource (unique among all its registered types)
        :param context_var: if not ``None``, make ``value`` available on the application context
                             with this name
        :param types: type(s) to register the resource as (omit to use the type of ``value``)
        :return: the resource object
        :raises ResourceConflict: if the resource conflicts with an existing one in any way
        """

        assert value is not None, 'value must not be None'
        return self._add_resource(value, alias, context_var, types, None)

    @asynchronous
    def add_lazy_resource(self, creator: Callable[['Context'], Any],
                          types: Union[Union[str, type], Iterable[Union[str, type]]],
                          alias: str='default', context_var: str=None) -> Resource:
        """
        Adds a "lazy" or "contextual" resource. Instead of a concrete resource value, you supply a
        creator callable which is called with a context (either this context or a subcontext) as
        its argument when there is either a matching resource request or the given context variable
        is accessed. The value will be cached so the creator callable will only be called once per
        context instance.

        The creator callable can **NOT** be a coroutine function.

        :param creator: a callable taking a context instance as argument
        :param types: type name, class or an iterable of either
        :param context_var: name of the context property
        :return: the resource object
        :raises ResourceConflict: if there is an existing resource creator for the given
                                  types or context variable
        """

        assert callable(creator), 'creator must be callable'
        assert not iscoroutinefunction(creator), 'creator cannot be a coroutine function'
        return self._add_resource(None, alias, context_var, types, creator)

    @asynchronous
    def remove_resource(self, resource: Resource):
        """
        Removes the given resource from the collection and dispatches a "resource_removed" event.

        :param resource: the resource to be removed
        :raises LookupError: the given resource was not in the collection
        """

        try:
            for typename in resource.types:
                del self._resources[typename][resource.alias]
        except KeyError:
            raise LookupError('{!r} not found in this context'.format(resource)) from None

        # Remove the creator from the resource creators
        if resource.creator is not None:
            del self._resource_creators[resource.context_var]

        # Remove the attribute from this context
        if resource.context_var and resource.context_var in self.__dict__:
            delattr(self, resource.context_var)

        self.dispatch('resource_removed', resource.types, resource.alias, False)

    def _get_resource(self, resource_type: str, alias: str):
        resource = self._resources.get(resource_type, {}).get(alias)
        if resource is None and self._parent is not None:
            resource = self._parent._get_resource(resource_type, alias)

        return resource

    @asynchronous
    def request_resource(self, type: Union[str, type], alias: str='default', *,
                         timeout: Union[int, float, None]=10):
        """
        Requests a resource matching the given type and alias.
        If no such resource was not found, this method will wait ``timeout`` seconds for it to
        become available.

        :param type: type of the requested resource
        :param alias: alias of the requested resource
        :param timeout: the timeout in seconds
        :return: the value contained by the requested resource (**NOT** a Resource instance)
        """

        if not type:
            raise ValueError('type must be a type or a nonempty string')
        if not alias:
            raise ValueError('alias must be a nonempty string')

        assert timeout is None or timeout >= 0, 'timeout cannot be negative'
        resource_type = qualified_name(type) if not isinstance(type, str) else type
        handle = event = start_time = None
        resource = self._get_resource(resource_type, alias)
        while resource is None:
            if not handle:
                event = asyncio.Event()
                start_time = time.monotonic()
                handle = self.add_listener('resource_added', lambda e: event.set())
            try:
                delay = timeout - (time.monotonic() - start_time) if timeout is not None else None
                yield from asyncio.wait_for(event.wait(), delay)
            except asyncio.TimeoutError:
                self.remove_listener(handle)
                raise ResourceNotFound(resource_type, alias)

            resource = self._get_resource(resource_type, alias)

        if handle:
            self.remove_listener(handle)

        return resource.get_value(self)
