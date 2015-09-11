from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from typing import Dict, Any, Union
import asyncio

from .util import qualified_name, PluginContainer
from .context import Context

__all__ = 'Component', 'ContainerComponent'

component_types = PluginContainer('asphalt.components')


class Component(metaclass=ABCMeta):
    """This is the base class for all Asphalt components."""

    __slots__ = ()

    @abstractmethod
    def start(self, ctx: Context):
        """
        This method is called on application start. It can be a coroutine.

        The context can be used to:
          * add context event listeners (:meth:`~Context.add_event_listener`)
          * add resources (:meth:`~Context.add_resource`)
          * add lazily created resources (:meth:`~Context.add_lazy_resource`)
          * request resources (:meth:`~Context.request_resource`)

        If the component requests any resources, it is advisable to first add all the resources
        it can before requesting any. This will speed up the dependency resolution and prevent
        deadlocks.

        :param ctx: the context for which the component was created
        """


class ContainerComponent(Component):
    __slots__ = 'child_components', 'component_config'

    def __init__(self, components: Dict[str, Any]=None):
        self.child_components = OrderedDict()
        self.component_config = components or {}

    def add_component(self, alias: str, type: Union[str, type]=None, **kwargs):
        """
        Instantiates a component using :func:`create_component` and adds it as a child component
        of this container.

        If the second argument is omitted, the value of ``alias`` is used as its value.

        The locally given configuration can be overridden by component configuration parameters
        supplied to the Application constructor (the ``components`` argument).

        :param alias: a name for the component instance, unique within this container
        :param type: entry point name or :cls:`Component` subclass or a textual reference to one
        """

        if not isinstance(alias, str) or not alias:
            raise TypeError('component_alias must be a nonempty string')
        if alias in self.child_components:
            raise ValueError('there is already a child component named "{}"'.format(alias))

        # Allow the external configuration to override the constructor arguments
        kwargs.update(self.component_config.get(alias, {}))

        component = create_component(type or alias, **kwargs)
        self.child_components[alias] = component

    def start(self, ctx: Context):
        # Automatically create any components that have configuration specified for them but have
        # not been manually added
        for alias in self.component_config:
            self.add_component(alias)

        # Start all the child components and return a Future which completes when they all have
        # started, or None
        tasks = []
        for component in self.child_components.values():
            retval = component.start(ctx)
            if retval is not None:
                tasks.append(retval)

        return asyncio.gather(*tasks) if tasks else None


def create_component(type: Union[str, type], **kwargs) -> Component:
    """
    Instantiates a component.

    The ``type`` argument can either be a :class:`Component` subclass or
    a component type name, declared as an ``asphalt.components`` entry point, in which case the
    component class is retrieved by loading the entry point. If the value is omitted, the value
    of ``alias`` is used as the entry point name to look up the class.

    Any extra keyword arguments are passed directly to the constructor of the component class.

    :param type: entry point name or :cls:`Component` subclass or a textual reference to one
    """

    component_class = component_types.resolve(type)
    if not issubclass(component_class, Component):
        raise TypeError('the component type must be a subclass of {}'.
                        format(qualified_name(Component)))

    return component_class(**kwargs)
