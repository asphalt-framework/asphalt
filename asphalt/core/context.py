from asyncio import iscoroutinefunction
from collections import defaultdict
from abc import ABCMeta, abstractmethod
from typing import Optional, Callable, Any
from enum import Enum

from .util import asynchronous
from .event import EventSourceMixin, Event
from .resource import ResourceCollection, ResourceEventType, ResourceEvent, ResourceConflict
from .router import Endpoint

__all__ = 'ContextScope', 'Context', 'ApplicationContext', 'TransportContext', 'HandlerContext'


class ContextScope(Enum):
    application = 1
    transport = 2
    handler = 3


class Context(EventSourceMixin, metaclass=ABCMeta):
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
        super().__init__({'started': Event, 'finished': Event})
        self._parent = parent
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
