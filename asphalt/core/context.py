from asyncio import iscoroutinefunction, coroutine
from collections import defaultdict
from abc import ABCMeta, abstractmethod
from typing import Optional, Callable, Any, Sequence, Dict
from enum import Enum

from .util import qualified_name, asynchronous
from .resource import ResourceCollection, ResourceEventType, ResourceEvent, ResourceConflict
from .router import Endpoint

__all__ = ('ContextScope', 'ContextEventType', 'CallbackPriority', 'Context',
           'ApplicationContext', 'TransportContext', 'HandlerContext')


class ContextScope(Enum):
    application = 1
    transport = 2
    handler = 3


class ContextEventType(Enum):
    started = 1
    finished = 2


class CallbackPriority(Enum):
    first = 1
    neutral = 2
    last = 3


class EventCallback:
    __slots__ = 'event', 'func', 'args', 'kwargs', 'position'

    def __init__(self, event_type: ContextEventType, func: Callable[['Context'], Any],
                 args: Sequence[Any], kwargs: Dict[str, Any],
                 position: CallbackPriority=CallbackPriority.neutral):
        self.event = event_type
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.position = position

    def __lt__(self, other):
        if isinstance(other, EventCallback) and self.event == other.event:
            return self.position.value < other.position.value
        return NotImplemented  # pragma: no cover

    def __repr__(self):
        return ('EventCallback(event={0.event.name}, func={1}, args={0.args!r}, '
                'kwargs={0.kwargs!r}, position={0.position.name})'.
                format(self, qualified_name(self.func)))


class Context(metaclass=ABCMeta):
    """
    Contexts give request handlers and callbacks access to resources.

    Contexts are stacked in a way that accessing an attribute that is not present in the current
    context causes the attribute to be looked up in the parent instance and so on, until the
    attribute is found (or ``AttributeError`` is raised).

    :ivar BaseException exception: the exception that occurred before exiting this context
                                   (available to finish callbacks)
    :ivar ResourceCollection resources: the resource collection
    """

    exception = None  # type: BaseException

    def __init__(self, parent: Optional['Context']):
        super().__init__()
        self._parent = parent
        self._scope_callbacks = (parent._scope_callbacks if parent is not None else
                                 defaultdict(list))
        self._local_callbacks = defaultdict(list)  # type: Dict[ContextEventType, List]
        self._handled_context_events = set()  # type: Set[ContextEventType]
        self._property_creators = defaultdict(dict)
        self.resources = parent.resources if parent is not None else ResourceCollection()

    @property
    @abstractmethod
    def scope(self) -> ContextScope:  # pragma: no cover
        pass

    def __getattr__(self, name):
        creator = self._property_creators[self.scope].get(name)
        if creator is not None:
            value = creator(self)
            setattr(self, name, value)
            return value

        if self._parent is not None:
            return getattr(self._parent, name)

        raise AttributeError('no such context property: {}'.format(name))

    def add_default_callback(
            self, scope: ContextScope, event_type: ContextEventType,
            func: Callable[['Context'], Any], args: Sequence[Any]=(),
            kwargs: Dict[str, Any]=None, position: CallbackPriority=CallbackPriority.neutral):
        """
        Adds a callback to be called by :meth:`run_callbacks` whenever the given event occurs in
        any context of the given scope. The callback may be a coroutine.

        When exactly these callbacks are called depends on the code that handles each particular
        context.

        For the ``application`` scope, use :meth:`add_callback` instead.
        """

        assert scope is not ContextScope.application, \
            'cannot add default callbacks on the application scope -- use add_callback() ' \
            'instead'.format(event_type.name[:-2])
        callback = EventCallback(ContextEventType.finished, func, args, kwargs or {}, position)
        collection = self._scope_callbacks[(scope, event_type)]
        collection.append(callback)
        collection.sort()

    def add_callback(self, event_type: ContextEventType, func: Callable[['Context'], Any],
                     args: Sequence[Any]=(), kwargs: Dict[str, Any]=None,
                     priority: CallbackPriority=CallbackPriority.neutral):
        """
        Adds a callback to be called by :meth:`run_callbacks` whenever the given event occurs in
        this particular context instance.

        When exactly these callbacks are called depends on the code that handles each particular
        context.
        """

        if event_type in self._handled_context_events:
            raise ValueError('cannot add {} callbacks to this context any more'.
                             format(event_type.name))

        callback = EventCallback(ContextEventType.finished, func, args, kwargs or {}, priority)
        self._local_callbacks[event_type].append(callback)

    @coroutine
    def run_callbacks(self, event_type: ContextEventType):
        """
        Runs both the default callbacks and instance level callbacks for the given event.
        The list of callbacks is sorted first based on its priority.

        This method should only be run by whatever component created the context.
        In other words, don't call this from your business logic.
        """

        if event_type in self._handled_context_events:
            raise ValueError('the {} callbacks for this context have already been run'.
                             format(event_type.name))
        self._handled_context_events.add(event_type)

        callbacks = sorted(self._scope_callbacks[(self.scope, event_type)] +
                           self._local_callbacks[event_type])
        for callback in callbacks:
            retval = callback.func(self, *callback.args, **callback.kwargs)
            if retval is not None:
                yield from retval

    @asynchronous
    def add_lazy_property(self, scope: ContextScope, context_var: str,
                          creator: Callable[['Context'], Any]):
        """
        Adds a "lazy property" creator. When accessing the named property of a context of the
        given scope, the given callable will be used to create the value. The value will be
        cached in the context instance so the creator callable will only be called once for
        every context instance and property combination.

        :param scope: scope or scopes in which the creator should work in
        :param creator: a callable taking a context instance as argument
        :param context_var: name of the context property
        :raises ValueError: if there is an existing creator for the given scope and context_var
        """

        assert callable(creator), 'creator must be callable'
        assert not iscoroutinefunction(creator), 'creator cannot be a coroutine function'
        assert isinstance(context_var, str) and context_var, 'contextvar must be a nonempty string'

        if context_var in self._property_creators[scope]:
            raise ValueError('there is already a lazy property for "{}" on the '
                             '{} scope'.format(context_var, scope.name))
        self._property_creators[scope][context_var] = creator


class ApplicationContext(Context):
    """
    The default application level context class.

    :param settings: application specific settings
    """

    def __init__(self, settings: dict):
        super().__init__(None)
        self.settings = settings
        self.resources.add_listener(ResourceEventType.added, self.__resource_added)
        self.resources.add_listener(ResourceEventType.removed, self.__resource_removed)

    def __resource_added(self, event: ResourceEvent):
        resource = event.resource
        if resource.context_var:
            # Check that there is no existing variable by that name
            if event.resource.context_var in dir(self):
                raise ResourceConflict('{!r} conflicts with an application context property'.
                                       format(resource), resource)

            # Check that there is no existing lazy property by that name
            if resource.context_var in self._property_creators[self.scope]:
                raise ResourceConflict(
                    '{!r} conflicts with an application scoped lazy property'.
                    format(resource), resource)

            setattr(self, resource.context_var, resource.value)

    def __resource_removed(self, event: ResourceEvent):
        resource = event.resource
        if resource.context_var and resource.context_var in self.__dict__:
            delattr(self, resource.context_var)

    @property
    def scope(self):
        return ContextScope.application


class TransportContext(Context):
    """
    The default transport level context class.

    :param parent: the application context
    """

    def __init__(self, parent: ApplicationContext):
        super().__init__(parent)

    @property
    def scope(self):
        return ContextScope.transport


class HandlerContext(Context):
    """
    The default handler level context class.

    :param parent: the transport context
    :param endpoint: the endpoint being handled
    """

    def __init__(self, parent: TransportContext, endpoint: Endpoint):
        super().__init__(parent)
        self.endpoint = endpoint

    @property
    def scope(self):
        return ContextScope.handler
