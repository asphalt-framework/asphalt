from __future__ import annotations

__all__ = ("Component", "ContainerComponent", "CLIApplicationComponent")

import asyncio
import sys
from abc import ABCMeta, abstractmethod
from asyncio import Future
from collections import OrderedDict
from traceback import print_exception
from typing import Any, Dict, Optional, Type, Union
from warnings import warn

from typeguard import check_argument_types

from asphalt.core.context import Context
from asphalt.core.utils import PluginContainer, merge_config, qualified_name


class Component(metaclass=ABCMeta):
    """This is the base class for all Asphalt components."""

    __slots__ = ()

    @abstractmethod
    async def start(self, ctx: Context) -> None:
        """
        Perform any necessary tasks to start the services provided by this component.

        In this method, components typically use the context to:
          * add resources and/or resource factories to it
            (:meth:`~asphalt.core.context.Context.add_resource` and
            :meth:`~asphalt.core.context.Context.add_resource_factory`)
          * request resources from it asynchronously
            (:meth:`~asphalt.core.context.Context.request_resource`)

        It is advisable for Components to first add all the resources they can to the context
        before requesting any from it. This will speed up the dependency resolution and prevent
        deadlocks.

        :param ctx: the containing context for this component
        """


class ContainerComponent(Component):
    """
    A component that can contain other components.

    :param components: dictionary of component alias ⭢ component configuration dictionary

    :ivar child_components: dictionary of component alias ⭢ :class:`Component` instance (of child
        components added with :meth:`add_component`)
    :vartype child_components: Dict[str, Component]
    :ivar component_configs: dictionary of component alias ⭢ externally provided component
        configuration
    :vartype component_configs: Dict[str, Optional[Dict[str, Any]]]
    """

    __slots__ = "child_components", "component_configs"

    def __init__(self, components: Dict[str, Optional[Dict[str, Any]]] = None) -> None:
        assert check_argument_types()
        self.child_components: OrderedDict[str, Component] = OrderedDict()
        self.component_configs = components or {}

    def add_component(
        self, alias: str, type: Union[str, Type] = None, **config
    ) -> None:
        """
        Add a child component.

        This will instantiate a component class, as specified by the ``type`` argument.

        If the second argument is omitted, the value of ``alias`` is used as its value.

        The locally given configuration can be overridden by component configuration parameters
        supplied to the constructor (via the ``components`` argument).

        When configuration values are provided both as keyword arguments to this method and
        component configuration through the ``components`` constructor argument, the configurations
        are merged together using :func:`~asphalt.core.util.merge_config` in a way that the
        configuration values from the ``components`` argument override the keyword arguments to
        this method.

        :param alias: a name for the component instance, unique within this container
        :param type: entry point name or :class:`Component` subclass or a ``module:varname``
            reference to one
        :param config: keyword arguments passed to the component's constructor

        """
        assert check_argument_types()
        if not isinstance(alias, str) or not alias:
            raise TypeError("component_alias must be a nonempty string")
        if alias in self.child_components:
            raise ValueError(f'there is already a child component named "{alias}"')

        config["type"] = type or alias

        # Allow the external configuration to override the constructor arguments
        override_config = self.component_configs.get(alias) or {}
        config = merge_config(config, override_config)

        component = component_types.create_object(**config)
        self.child_components[alias] = component

    async def start(self, ctx: Context) -> None:
        """
        Create child components that have been configured but not yet created and then calls their
        :meth:`~Component.start` methods in separate tasks and waits until they have completed.

        """
        for alias in self.component_configs:
            if alias not in self.child_components:
                self.add_component(alias)

        tasks = [component.start(ctx) for component in self.child_components.values()]
        if tasks:
            await asyncio.gather(*tasks)


class CLIApplicationComponent(ContainerComponent):
    """
    Specialized subclass of :class:`.ContainerComponent` for command line tools.

    Command line tools and similar applications should use this as their root component and
    implement their main code in the :meth:`run` method.

    When all the subcomponents have been started, :meth:`run` is started as a new task.
    When the task is finished, the application will exit using the return value as its exit code.

    If :meth:`run` raises an exception, a stack trace is printed and the exit code will be set
    to 1. If the returned exit code is out of range or of the wrong data type, it is set to 1 and a
    warning is emitted.
    """

    async def start(self, ctx: Context) -> None:
        def run_complete(f: Future[int | None]) -> None:
            # If run() raised an exception, print it with a traceback and exit with code 1
            exc = f.exception()
            if exc is not None:
                print_exception(type(exc), exc, exc.__traceback__)
                sys.exit(1)

            retval = f.result()
            if isinstance(retval, int):
                if 0 <= retval <= 127:
                    sys.exit(retval)
                else:
                    warn("exit code out of range: %d" % retval)
                    sys.exit(1)
            elif retval is not None:
                warn(
                    "run() must return an integer or None, not %s"
                    % qualified_name(retval.__class__)
                )
                sys.exit(1)
            else:
                sys.exit(0)

        def start_run_task() -> None:
            task = ctx.loop.create_task(self.run(ctx))
            task.add_done_callback(run_complete)

        await super().start(ctx)
        ctx.loop.call_later(0.1, start_run_task)

    @abstractmethod
    async def run(self, ctx: Context) -> Optional[int]:
        """
        Run the business logic of the command line tool.

        Do not call this method yourself.

        :return: the application's exit code (0-127; ``None`` = 0)
        """


component_types = PluginContainer("asphalt.components", Component)
