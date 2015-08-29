from asyncio import coroutine, iscoroutinefunction
from collections import defaultdict
from typing import Union, Iterable, Optional, Callable, Any
from enum import Enum
import asyncio
import time

from .util import qualified_name, asynchronous

__all__ = ('Resource', 'ResourceEventType', 'ResourceEvent', 'ResourceEventListener',
           'ResourceConflict', 'ResourceNotFoundError', 'ResourceCollection')


class ResourceEventType(Enum):
    added = 1
    removed = 2


class Resource:
    __slots__ = 'types', 'alias', 'value', 'context_var'

    def __init__(self, value, types: tuple, alias: str, context_var: Optional[str]):
        self.types = types
        self.value = value
        self.alias = alias
        self.context_var = context_var

    def __repr__(self):
        return '{0.__class__.__name__}({0})'.format(self)

    def __str__(self):
        return ('types={0.types!r}, alias={0.alias!r}, value={0.value!r}, '
                'context_var={0.context_var!r}'.format(self))


class ResourceEvent:
    __slots__ = 'type', 'resource'

    def __init__(self, type: ResourceEventType, resource: Resource):
        super().__init__()
        self.type = type
        self.resource = resource


class ResourceEventListener:
    __slots__ = '_callbacks', 'callback'

    def __init__(self, callbacks: list, callback: Callable[[ResourceEvent], None]):
        self._callbacks = callbacks
        self.callback = callback

    def unlisten(self):
        self._callbacks.remove(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.unlisten()


class ResourceConflict(Exception):
    def __init__(self, error: str, resource: Resource):
        super().__init__(error, resource)
        self.resource = resource

    def __str__(self):
        return self.args[0]


class ResourceNotFoundError(LookupError):
    def __init__(self, type: str, alias: str):
        super().__init__(type, alias)
        self.type = type
        self.alias = alias

    def __str__(self):
        return 'no matching resource was found for type={0.type!r} alias={0.alias!r}'.format(self)


class ResourceCollection:
    __slots__ = '_resources', '_resource_creators', '_event_listeners'

    def __init__(self):
        self._resources = defaultdict(dict)  # type: Dict[str, Dict[str, Resource]]
        self._resource_creators = defaultdict(dict)
        self._event_listeners = defaultdict(list)

    def add_listener(self, event_type: ResourceEventType,
                     callback: Callable[[ResourceEvent], Any]):
        """
        Adds an event listener to be called when resources have been added or removed to
        this collection.

        A listener can veto the addition of a resource by raising
        :exc:`ResourceConflict` (which is then reraised after cancelling the operation).
        Any other exception is simply passed through.

        :param event_type: the type of event to listen for
        :param callback: a callable taking the event type and a list of resources as arguments
        :return: a listener handle (call its ``unlisten`` method to unlisten)
        """

        assert not iscoroutinefunction(callback), "callback can't be a coroutine function"
        listeners = self._event_listeners[event_type]
        listener = ResourceEventListener(listeners, callback)
        listeners.append(listener)
        return listener

    def _trigger_event(self, event_type: ResourceEventType, resource: Resource):
        event = ResourceEvent(event_type, resource)
        for listener in self._event_listeners[event_type]:
            listener.callback(event)

    def add(self, value, alias: str='default', context_var: str=None, *,
            extra_types: Union[Union[str, type], Iterable[Union[str, type]]]=()) -> Resource:
        """
        Adds a resource to the collection and queues an "added" event to be sent.

        :param value: the actual resource value
        :param alias: an identifier for this resource (unique among all its registered types)
        :param context_var: if not ``None``, make ``value`` available on the application context
                             with this name
        :param extra_types: additional type(s) to register the resource as
        :return: the resulting Resource instance
        :raises ResourceConflict: if the resource conflicts with an existing one in any way
        """

        assert value is not None, 'value must not be None'
        assert isinstance(alias, str) and alias, 'alias must be a nonempty string'
        assert context_var is None or isinstance(context_var, str),\
            'context_var must be a nonempty string or None'
        types = (extra_types,) if isinstance(extra_types, (str, type)) else extra_types
        types = (type(value),) + types
        types = tuple(t if isinstance(t, str) else qualified_name(t) for t in types)

        resource = Resource(value, types, alias, context_var)

        # Check for name conflicts
        for typename in types:
            conflicting = self._resources[typename].get(alias)
            if conflicting is not None:
                raise ResourceConflict('"{}" conflicts with {!r}'.format(alias, conflicting),
                                       conflicting)

        # Register the resource
        for typename in types:
            self._resources[typename][alias] = resource

        # Signal listeners that a resource has been added
        try:
            self._trigger_event(ResourceEventType.added, resource)
        except ResourceConflict:
            # In case of a resource conflict, remove the resource
            self.remove(resource)
            raise

        return resource

    @asynchronous
    def remove(self, resource: Resource):
        """
        Removes the given resource from the collection and queues a "removed" event to be sent.

        :param resource: the resource to be removed
        :raises LookupError: the given resource was not in the collection
        """

        try:
            for typename in resource.types:
                del self._resources[typename][resource.alias]
        except KeyError:
            raise LookupError('{!r} not found in this collection'.format(resource)) from None

        # Signal listeners that a resource has been removed
        self._trigger_event(ResourceEventType.removed, resource)

    @asynchronous
    @coroutine
    def request(self, type: Union[str, type], alias: str='default', *,
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
        resource = self._resources.get(resource_type, {}).get(alias)
        while resource is None:
            if not handle:
                event = asyncio.Event()
                start_time = time.monotonic()
                handle = self.add_listener(ResourceEventType.added, lambda e: event.set())
            try:
                delay = timeout - (time.monotonic() - start_time) if timeout is not None else None
                yield from asyncio.wait_for(event.wait(), delay)
            except asyncio.TimeoutError:
                handle.unlisten()
                raise ResourceNotFoundError(resource_type, alias)

            resource = self._resources.get(resource_type, {}).get(alias)

        if handle:
            handle.unlisten()

        return resource.value
