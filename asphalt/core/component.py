from asyncio import coroutine
from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from typing import Dict, Any, Union
import asyncio

from .util import PluginContainer, merge_config
from .context import Context

__all__ = 'Component', 'ContainerComponent', 'component_types'


class Component(metaclass=ABCMeta):
    """This is the base class for all Asphalt components."""

    __slots__ = ()

    @abstractmethod
    def start(self, ctx: Context):
        """
        This method is the entry point to a component. It can be a coroutine.

        The context can be used to:
          * add context event listeners (:meth:`~Context.add_listener`)
          * publish resources (:meth:`~Context.publish_resource` and
            :meth:`~Context.publish_lazy_resource`)
          * request resources (:meth:`~Context.request_resource`)

        It is advisable for Components to first publish all the resources they can before
        requesting any. This will speed up the dependency resolution and prevent deadlocks.

        :param ctx: the containing context for this component
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
        kwargs = merge_config(kwargs, self.component_config.get(alias, {}))

        component = component_types.create_object(type or alias, **kwargs)
        self.child_components[alias] = component

    @coroutine
    def start(self, ctx: Context):
        """
        Creates child components that have been configured but not yet created and then calls their
        :meth:`Component.start` methods in separate tasks and waits until they have completed.
        """

        for alias in self.component_config:
            if alias not in self.child_components:
                self.add_component(alias)

        if self.child_components:
            tasks = []
            for component in self.child_components.values():
                retval = component.start(ctx)
                if retval is not None:
                    tasks.append(retval)

            if tasks:
                yield from asyncio.gather(*tasks)


component_types = PluginContainer('asphalt.components', Component)
