from __future__ import annotations

__all__ = ("Component", "ContainerComponent", "CLIApplicationComponent")

from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from contextlib import AsyncExitStack
from traceback import print_exception
from typing import Any
from warnings import warn

from anyio import create_memory_object_stream, create_task_group
from anyio.abc import TaskGroup

from .context import Context
from .utils import PluginContainer, merge_config, qualified_name


class Component(metaclass=ABCMeta):
    """This is the base class for all Asphalt components."""

    _task_group = None

    async def __aenter__(self) -> Component:
        if self._task_group is not None:
            raise RuntimeError("Component already entered")

        async with AsyncExitStack() as exit_stack:
            tg = create_task_group()
            self._task_group = await exit_stack.enter_async_context(tg)
            self._exit_stack = exit_stack.pop_all()

        return self

    async def __aexit__(self, exc_type, exc_value, exc_tb):
        if self._task_group is None:
            raise RuntimeError("Component not entered")

        self._task_group = None
        return await self._exit_stack.__aexit__(exc_type, exc_value, exc_tb)

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

    @property
    def task_group(self) -> TaskGroup:
        return self._task_group


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

    def __init__(self, components: dict[str, dict[str, Any] | None] | None = None) -> None:
        self.child_components: OrderedDict[str, Component] = OrderedDict()
        self.component_configs = components or {}

    def add_component(self, alias: str, type: str | type | None = None, **config) -> None:
        """
        Add a child component.

        This will instantiate a component class, as specified by the ``type`` argument.

        If the second argument is omitted, the value of ``alias`` is used as its value.

        The locally given configuration can be overridden by component configuration parameters
        supplied to the constructor (via the ``components`` argument).

        When configuration values are provided both as keyword arguments to this method and
        component configuration through the ``components`` constructor argument, the configurations
        are merged together using :func:`~asphalt.core.utils.merge_config` in a way that the
        configuration values from the ``components`` argument override the keyword arguments to
        this method.

        :param alias: a name for the component instance, unique within this container
        :param type: entry point name or :class:`Component` subclass or a ``module:varname``
            reference to one
        :param config: keyword arguments passed to the component's constructor

        """
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

        async def start_child_components():
            async with create_task_group() as tg:
                for component in self.child_components.values():
                    component._task_group = self._task_group
                    tg.start_soon(component.start, ctx)

        await start_child_components()


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
        await super().start(ctx)

        async def run(exit_code):
            try:
                retval = await self.run(ctx)
            except Exception as exc:
                print_exception(type(exc), exc, exc.__traceback__)
                exit_code.send_nowait(1)
                return

            if isinstance(retval, int):
                if 0 <= retval <= 127:
                    exit_code.send_nowait(retval)
                else:
                    warn("exit code out of range: %d" % retval)
                    exit_code.send_nowait(1)
            elif retval is not None:
                warn(
                    "run() must return an integer or None, not %s"
                    % qualified_name(retval.__class__)
                )
                exit_code.send_nowait(1)
            else:
                exit_code.send_nowait(0)

        send_stream, receive_stream = create_memory_object_stream[int](max_buffer_size=1)
        self._exit_code = receive_stream
        self.task_group.start_soon(run, send_stream)

    async def exit_code(self) -> int:
        return await self._exit_code.receive()

    @abstractmethod
    async def run(self, ctx: Context) -> int | None:
        """
        Run the business logic of the command line tool.

        Do not call this method yourself.

        :return: the application's exit code (0-127; ``None`` = 0)
        """


component_types = PluginContainer("asphalt.components", Component)
