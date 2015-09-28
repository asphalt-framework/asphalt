from typing import Optional, Callable, Any, Union, Iterable, Container
from asyncio import get_event_loop, coroutine, iscoroutinefunction
from collections import defaultdict
import asyncio
import time

from .util import qualified_name, asynchronous
from .event import EventSource, Event

__all__ = ('Resource', 'ResourceEvent', 'ResourceConflict', 'ResourceNotFound',
           'ContextFinishEvent', 'Context')


class Resource:
    """A handle that can be used to remove a resource from a context."""

    __slots__ = 'value', 'types', 'alias', 'context_attr', 'creator'

    def __init__(self, value, types: Iterable[str], alias: str, context_attr: Optional[str],
                 creator: Callable[['Context'], Any]=None):
        self.value = value
        self.types = types
        self.alias = alias
        self.context_attr = context_attr
        self.creator = creator

    def get_value(self, ctx: 'Context'):
        if self.value is None and self.creator is not None:
            self.value = self.creator(ctx)
            if self.context_attr:
                setattr(ctx, self.context_attr, self.value)

        return self.value

    def __repr__(self):
        return '{0.__class__.__name__}({0})'.format(self)

    def __str__(self):
        return ('types={0.types!r}, alias={0.alias!r}, value={0.value!r}, '
                'context_attr={0.context_attr!r}, lazy={1}'.format(self, self.creator is not None))


class ResourceEvent(Event):
    """
    Dispatched when a resource has been published to or removed from a context.

    :ivar Context source: the relevant context
    :ivar Container[str] types: names of the types for the resource
    :ivar str alias: the alias of the resource
    :ivar bool lazy: ``True`` if this is a lazily created resource, ``False`` if not
    """

    __slots__ = 'types', 'alias', 'lazy'

    def __init__(self, source: 'Context', topic: str, types: Container[str], alias: str,
                 lazy: bool):
        super().__init__(source, topic)
        self.types = types
        self.alias = alias
        self.lazy = lazy


class ResourceConflict(Exception):
    """
    Raised when a new resource that is being published conflicts with an existing resource or
    context variable.
    """


class ResourceNotFound(LookupError):
    """Raised when a resource request cannot be fulfilled within the allotted time."""

    def __init__(self, type: str, alias: str):
        super().__init__(type, alias)
        self.type = type
        self.alias = alias

    def __str__(self):
        return 'no matching resource was found for type={0.type!r} alias={0.alias!r}'.format(self)


class ContextFinishEvent(Event):
    """
    Dispatched when a context has served its purpose and is being torn down.

    :ivar BaseException exception: the exception that caused the context to finish (or ``None``)
    """

    __slots__ = 'exception'

    def __init__(self, source: 'Context', topic: str, exception: Optional[BaseException]):
        super().__init__(source, topic)
        self.exception = exception


class Context(EventSource):
    """
    Contexts give request handlers and callbacks access to resources.

    Contexts are stacked in a way that accessing an attribute that is not present in the current
    context causes the attribute to be looked up in the parent instance and so on, until the
    attribute is found (or ``AttributeError`` is raised).

    Supported events:
      * finished (:class:`~asphalt.core.event.Event`): the context has served its purpose and is
        being discarded
      * resource_published (:class:`ResourceEvent`): a resource has been published in this context
      * resource_removed (:class:`ResourceEvent`): a resource has been removed from this context

    :param parent: the parent context, if any
    :param default_timeout: default timeout for :meth:`request_resource` if omitted from the
                            call arguments
    """

    def __init__(self, parent: 'Context'=None, default_timeout: int=10):
        super().__init__()
        self._register_topics({
            'finished': ContextFinishEvent,
            'resource_published': ResourceEvent,
            'resource_removed': ResourceEvent
        })

        self._parent = parent
        self._resources = defaultdict(dict)  # type: Dict[str, Dict[str, Resource]]
        self._resource_creators = {}  # type: Dict[str, Callable[[Context], Any]
        self.default_timeout = default_timeout

        # Forward resource events from the parent(s)
        if parent is not None:
            parent.add_listener('resource_published', self.dispatch)
            parent.add_listener('resource_removed', self.dispatch)

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
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        get_event_loop().run_until_complete(self.dispatch('finished', exc_val))

    @coroutine
    def __aenter__(self):
        return self

    @coroutine
    def __aexit__(self, exc_type, exc_val, exc_tb):
        yield from self.dispatch('finished', exc_val)

    @coroutine
    def _publish_resource(self, value, alias: str, context_attr: str,
                          types: Iterable[Union[str, type]],
                          creator: Optional[Callable[['Context'], Any]]):
        assert isinstance(alias, str) and alias, 'alias must be a nonempty string'
        assert context_attr is None or isinstance(context_attr, str),\
            'context_attr must be a nonempty string or None'

        if isinstance(types, (str, type)):
            types = (types,)

        # Check for alias conflicts with existing resources
        types = tuple(t if isinstance(t, str) else qualified_name(t) for t in types)
        for typename in types:
            if alias in self._resources[typename]:
                raise ResourceConflict(
                    'this context has an existing resource of type {} using the alias "{}"'
                    .format(typename, alias))

        # Check for context attribute conflicts
        if context_attr:
            # Check that there is no existing attribute by that name
            if context_attr in dir(self):
                raise ResourceConflict(
                    'this context already has an attribute "{}"'.format(context_attr))

            # Check that there is no existing lazy resource using the same context attribute
            if context_attr in self._resource_creators:
                raise ResourceConflict(
                    'this context has an existing lazy resource using the attribute "{}"'
                    .format(context_attr))

        # Register the resource
        resource = Resource(value, types, alias, context_attr, creator)
        for typename in types:
            self._resources[typename][resource.alias] = resource

        if creator is not None and context_attr is not None:
            self._resource_creators[context_attr] = creator

        # Add the resource as an attribute of this context if context_attr is defined
        if creator is None and resource.context_attr:
            setattr(self, context_attr, value)

        yield from self.dispatch('resource_published', types, alias, False)
        return resource

    @asynchronous
    def publish_resource(
            self, value, alias: str='default', context_attr: str=None, *,
            types: Union[Union[str, type], Iterable[Union[str, type]]]=()) -> Resource:
        """
        Publishes a resource and dispatches a ``resource_published`` event.

        :param value: the actual resource value
        :param alias: name of this resource (unique among all its registered types)
        :param context_attr: name of the context attribute this resource will be accessible as
        :param types: type(s) to register the resource as (omit to use the type of ``value``)
        :return: the resource handle
        :raises ResourceConflict: if the resource conflicts with an existing one in any way
        """

        assert value is not None, 'value must not be None'
        if not types:
            types = [type(value)]

        return self._publish_resource(value, alias, context_attr, types, None)

    @asynchronous
    def publish_lazy_resource(self, creator: Callable[['Context'], Any],
                              types: Union[Union[str, type], Iterable[Union[str, type]]],
                              alias: str='default', context_attr: str=None) -> Resource:
        """
        Publishes a "lazy" or "contextual" resource and dispatches a ``resource_published`` event.
        Instead of a concrete resource value, you supply a creator callable which is called with a
        context object as its argument when the resource is being requested either via
        :meth:`request_resource` or by context attribute access.
        The return value of the creator callable will be cached so the creator will only be called
        once per context instance.

        .. note:: The creator callable can **NOT** be a coroutine function, as coroutines cannot
        be run as a side effect of attribute access.

        :param creator: a callable taking a context instance as argument
        :param types: type(s) to register the resource as
        :param context_attr: name of the context attribute this resource will be accessible as
        :return: the resource handle
        :raises ResourceConflict: if there is an existing resource creator for the given
                                  types or context variable
        """

        assert callable(creator), 'creator must be callable'
        assert not iscoroutinefunction(creator), 'creator cannot be a coroutine function'
        return self._publish_resource(None, alias, context_attr, types, creator)

    @asynchronous
    def remove_resource(self, resource: Resource):
        """
        Removes the given resource from the collection and dispatches a ``resource_removed`` event.

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
            del self._resource_creators[resource.context_attr]

        # Remove the attribute from this context
        if resource.context_attr and resource.context_attr in self.__dict__:
            delattr(self, resource.context_attr)

        yield from self.dispatch('resource_removed', resource.types, resource.alias, False)

    def _get_resource(self, resource_type: str, alias: str) -> Optional[Resource]:
        resource = self._resources.get(resource_type, {}).get(alias)
        if resource is None and self._parent is not None:
            resource = self._parent._get_resource(resource_type, alias)

        return resource

    @asynchronous
    def request_resource(self, type: Union[str, type], alias: str='default', *,
                         timeout: Union[int, float, None]=None, optional: bool=False):
        """
        Requests a resource matching the given type and alias.
        If no such resource was found, this method will wait ``timeout`` seconds for it to become
        available.

        :param type: type of the requested resource
        :param alias: alias of the requested resource
        :param timeout: the timeout (in seconds; omit to use the default timeout)
        :param optional: if ``True``, return None instead of raising an exception if no matching \
                         resource becomes available within the timeout period
        :return: the value contained by the requested resource
                 (**NOT** a :class:`Resource` instance)
        :raises ResourceNotFound: if the requested resource does not become available in the \
                                  allotted time
        """

        if not type:
            raise ValueError('type must be a type or a nonempty string')
        if not alias:
            raise ValueError('alias must be a nonempty string')

        timeout = timeout if timeout is not None else self.default_timeout
        assert timeout >= 0, 'timeout must be a positive integer'

        resource_type = qualified_name(type) if not isinstance(type, str) else type
        handle = event = start_time = None
        resource = self._get_resource(resource_type, alias)
        while resource is None:
            if not handle:
                event = asyncio.Event()
                start_time = time.monotonic()
                handle = self.add_listener('resource_published', lambda e: event.set())
            try:
                delay = timeout - (time.monotonic() - start_time) if timeout is not None else None
                yield from asyncio.wait_for(event.wait(), delay)
            except asyncio.TimeoutError:
                self.remove_listener(handle)
                if optional:
                    return None
                else:
                    raise ResourceNotFound(resource_type, alias)

            resource = self._get_resource(resource_type, alias)

        if handle:
            self.remove_listener(handle)

        return resource.get_value(self)
