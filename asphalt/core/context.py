import re
from asyncio import Future, TimeoutError, wait_for, ensure_future
from inspect import isawaitable, iscoroutine
from itertools import chain
from typing import Optional, Callable, Any, Union, Iterable, Sequence, Dict  # noqa

from collections import defaultdict
from typeguard import check_argument_types

from asphalt.core.event import Signal, Event
from asphalt.core.util import qualified_name

__all__ = ('Resource', 'ResourceEvent', 'ResourceConflict', 'ResourceNotFound',
           'ContextFinishEvent', 'Context')


class Resource:
    """
    Contains the resource value or its creator callable, plus some metadata.

    :ivar str alias: alias of the resource
    :ivar Sequence[str] types: type names the resource was registered with
    :ivar str context_attr: the context attribute of the resource
    :ivar creator: callable to create the value (in case of a lazy resource)
    :vartype creator: Callable[[Context], Any]
    """

    __slots__ = 'value', 'types', 'alias', 'context_attr', 'creator'

    def __init__(self, value, types: Sequence[str], alias: str, context_attr: Optional[str],
                 creator: Callable[['Context'], Any] = None):
        assert check_argument_types()
        self.value = value
        self.types = types
        self.alias = alias
        self.context_attr = context_attr
        self.creator = creator

    def get_value(self, ctx: 'Context'):
        assert check_argument_types()
        if self.value is None and self.creator is not None:
            self.value = self.creator(ctx)
            if iscoroutine(self.value):
                self.value = ensure_future(self.value)
            if self.context_attr:
                setattr(ctx, self.context_attr, self.value)

        return self.value

    def __repr__(self):
        return '{0.__class__.__name__}({0})'.format(self)

    def __str__(self):
        return ('types={0.types!r}, alias={0.alias!r}, value={0.value!r}, '
                'context_attr={0.context_attr!r}, creator={1}'
                .format(self, qualified_name(self.creator) if self.creator else None))


class ResourceEvent(Event):
    """
    Dispatched when a resource has been published to or removed from a context.

    :ivar Context source: the relevant context
    :ivar Resource resource: the resource that was published or removed
    """

    __slots__ = 'resource'

    def __init__(self, source: 'Context', topic: str, resource: Resource):
        assert check_argument_types()
        super().__init__(source, topic)
        self.resource = resource


class ResourceConflict(Exception):
    """
    Raised when a new resource that is being published conflicts with an existing resource or
    context variable.
    """


class ResourceNotFound(LookupError):
    """Raised when a resource request cannot be fulfilled within the allotted time."""

    def __init__(self, type: str, alias: str):
        assert check_argument_types()
        super().__init__(type, alias)
        self.type = type
        self.alias = alias

    def __str__(self):
        return 'no matching resource was found for type={0.type!r} alias={0.alias!r}'.format(self)


class ContextFinishEvent(Event):
    """
    Dispatched when a context has served its purpose and is being torn down.

    :ivar Optional[BaseException] exception: the exception that caused the context to finish, if
        any
    """

    __slots__ = 'exception'

    def __init__(self, source: 'Context', topic: str, exception: Optional[BaseException]):
        assert check_argument_types()
        super().__init__(source, topic)
        self.exception = exception


class Context:
    """
    Contexts give request handlers and callbacks access to resources.

    Contexts are stacked in a way that accessing an attribute that is not present in the current
    context causes the attribute to be looked up in the parent instance and so on, until the
    attribute is found (or :class:`AttributeError` is raised).

    Requesting or publishing of resources **MUST NOT** be attempted during or after the dispatch
    of the ``finished`` event.

    :param parent: the parent context, if any
    :param default_timeout: default timeout for :meth:`request_resource` if omitted from the call
        arguments

    :var Signal finished: a signal (:class:`ContextFinishEvent`) dispatched when the context has
        served its purpose and is being discarded
    :var Signal resource_published: a signal (:class:`ResourceEvent`) dispatched when a resource
        has been published in this context
    :var Signal resource_removed: a signal (:class:`ResourceEvent`): dispatched when a resource has
        been removed from this context
    """

    finished = Signal(ContextFinishEvent)
    resource_published = Signal(ResourceEvent)
    resource_removed = Signal(ResourceEvent)

    def __init__(self, parent: 'Context' = None, *, default_timeout: int = 5):
        assert check_argument_types()
        self._parent = parent
        self._resources = defaultdict(dict)  # type: Dict[str, Dict[str, Resource]]
        self._lazy_resources = {}  # type: Dict[str, Resource]
        self.default_timeout = default_timeout

    def __getattr__(self, name):
        resource = self._lazy_resources.get(name)
        if resource is not None:
            value = resource.get_value(self)
            setattr(self, name, value)
            return value

        if self._parent is not None:
            return getattr(self._parent, name)

        raise AttributeError('no such context variable: {}'.format(name))

    @property
    def parent(self) -> Optional['Context']:
        """Return the parent of this context or ``None`` if there is no parent context."""
        return self._parent

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.finished.dispatch(exc_val, return_future=True)

    def _publish_resource(self, value, alias: str, context_attr: str,
                          types: Iterable[Union[str, type]],
                          creator: Optional[Callable[['Context'], Any]]):
        assert isinstance(alias, str) and alias, 'alias must be a nonempty string'
        assert re.match(r'^\w+$', alias),\
            'alias can only contain alphanumeric characters and underscores'
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

            # Check that there is no existing lazy resource using the
            # same context attribute
            if context_attr in self._lazy_resources:
                raise ResourceConflict(
                    'this context has an existing lazy resource using the attribute "{}"'
                    .format(context_attr))

        # Register the resource
        resource = Resource(value, types, alias, context_attr, creator)
        for typename in types:
            self._resources[typename][resource.alias] = resource

        if creator is not None and context_attr is not None:
            self._lazy_resources[context_attr] = resource

        # Add the resource as an attribute of this context if context_attr is defined
        if creator is None and resource.context_attr:
            setattr(self, context_attr, value)

        self.resource_published.dispatch(resource)
        return resource

    def publish_resource(
            self, value, alias: str = 'default', context_attr: str = None, *,
            types: Union[Union[str, type], Iterable[Union[str, type]]] = ()) -> Resource:
        """
        Publish a resource and dispatch a ``resource_published``  event.

        :param value: the actual resource value
        :param alias: name of this resource (unique among all its registered types)
        :param context_attr: name of the context attribute this resource will be accessible as
        :param types: type(s) to register the resource as (omit to use the type of ``value``)
        :return: the resource handle
        :raises asphalt.core.context.ResourceConflict: if the resource conflicts with an existing
            one in any way

        """
        assert check_argument_types()
        assert value is not None, 'value must not be None'
        if not types:
            types = [type(value)]

        return self._publish_resource(value, alias, context_attr, types, None)

    def publish_lazy_resource(self, creator: Callable[['Context'], Any],
                              types: Union[Union[str, type], Iterable[Union[str, type]]],
                              alias: str = 'default', context_attr: str = None) -> Resource:
        """
        Publish a "lazy" or "contextual" resource and dispatch a ``resource_published`` event.

        Instead of a concrete resource value, you supply a creator callable which is called with a
        context object as its argument when the resource is being requested either via
        :meth:`request_resource` or by context attribute access.
        The return value of the creator callable will be cached so the creator will only be called
        once per context instance.

        If the creator callable is a coroutine function or returns an awaitable, it is resolved
        before storing the resource value and returning it to the requester. Note that this will
        **NOT** work when a context attribute has been specified for the resource.

        :param creator: a callable taking a context instance as argument
        :param types: type(s) to register the resource as
        :param alias: name of this resource (unique among all its registered types)
        :param context_attr: name of the context attribute this resource will be accessible as
        :return: the resource handle
        :raises asphalt.core.context.ResourceConflict: if there is an existing resource creator for
            the given types or context variable

        """
        assert check_argument_types()
        assert callable(creator), 'creator must be callable'
        return self._publish_resource(None, alias, context_attr, types, creator)

    def remove_resource(self, resource: Resource):
        """
        Remove the given resource from the collection and dispatch a ``resource_removed`` event.

        :param resource: the resource to be removed
        :raises LookupError: the given resource was not in the collection

        """
        assert check_argument_types()
        try:
            for typename in resource.types:
                del self._resources[typename][resource.alias]
        except KeyError:
            raise LookupError('{!r} not found in this context'.format(resource)) from None

        # Remove the creator from the resource creators
        if resource.creator is not None:
            del self._lazy_resources[resource.context_attr]

        # Remove the attribute from this context
        if resource.context_attr and resource.context_attr in self.__dict__:
            delattr(self, resource.context_attr)

        self.resource_removed.dispatch(resource)

    async def request_resource(self, type: Union[str, type], alias: str = 'default', *,
                               timeout: Union[int, None] = None):
        """
        Request a resource matching the given type and alias.

        If no such resource was found, this method will wait ``timeout`` seconds for it to become
        available. The timeout does not apply to resolving awaitables created by lazy resource
        creators.

        :param type: type of the requested resource
        :param alias: alias of the requested resource
        :param timeout: the timeout (in seconds; omit to use the context's default timeout)
        :return: the value contained by the requested resource (**NOT** a :class:`Resource`
            instance)
        :raises asphalt.core.context.ResourceNotFound: if the requested resource does not become
            available in the allotted time

        """
        assert check_argument_types()
        if not type:
            raise ValueError('type must be a type or a nonempty string')
        if not alias:
            raise ValueError('alias must be a nonempty string')

        resource_type = qualified_name(type) if not isinstance(type, str) else type
        timeout = timeout if timeout is not None else self.default_timeout
        assert timeout >= 0, 'timeout must be a positive integer'

        # Build a context chain from this context and its parents
        context_chain = [self]
        while context_chain[-1]._parent:
            context_chain.append(context_chain[-1]._parent)

        # First try to look up the resource in the context chain
        for ctx in context_chain:
            resource = ctx._resources.get(resource_type, {}).get(alias)
            if resource is not None:
                value = resource.get_value(self)
                return await value if isawaitable(value) else value

        # Listen to resource publish events in the whole chain and wait for the right kind of
        # resource to be published
        def resource_listener(event: ResourceEvent):
            if event.resource.alias == alias and resource_type in event.resource.types:
                future.set_result(event.resource)

        future = Future()
        for ctx in context_chain:
            ctx.resource_published.connect(resource_listener)

        try:
            resource = await wait_for(future, timeout)
        except TimeoutError:
            raise ResourceNotFound(resource_type, alias) from None
        else:
            value = resource.get_value(self)
            return await value if isawaitable(value) else value
        finally:
            for ctx in context_chain:
                ctx.resource_published.disconnect(resource_listener)

    def get_resources(self, type: Union[str, type] = None, *,
                      include_parents: bool = True) -> Sequence[Resource]:
        """
        Return the currently published resources specific to one type or all types.

        :param type: type of the resources to return, or ``None`` to return all resources
        :param include_parents: include the resources from parent contexts

        """
        resources = set(chain(*(value.values() for value in self._resources.values())))
        if include_parents and self._parent:
            resources = resources.union(self._parent.get_resources(type, include_parents=True))

        if type is not None:
            resource_type = qualified_name(type) if not isinstance(type, str) else type
            resources = (resource for resource in resources if resource_type in resource.types)

        return sorted(resources, key=lambda resource: (resource.types, resource.alias))
