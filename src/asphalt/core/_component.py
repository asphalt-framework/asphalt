from __future__ import annotations

from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from collections.abc import Coroutine
from contextlib import ExitStack
from dataclasses import dataclass, field
from logging import getLogger
from traceback import StackSummary
from types import FrameType
from typing import Any

from anyio import (
    CancelScope,
    create_task_group,
    get_current_task,
    get_running_tasks,
    sleep,
)
from anyio.abc import TaskStatus

from ._concurrent import start_service_task
from ._context import current_context
from ._exceptions import NoCurrentContext
from ._utils import PluginContainer, merge_config, qualified_name


class Component(metaclass=ABCMeta):
    """This is the base class for all Asphalt components."""

    __slots__ = ()

    @abstractmethod
    async def start(self) -> None:
        """
        Perform any necessary tasks to start the services provided by this component.

        In this method, components typically use the context to:
          * add resources and/or resource factories to it
            (:func:`add_resource` and :func:`add_resource_factory`)
          * get resources from it asynchronously
            (:func:`get_resource`)

        It is advisable for Components to first add all the resources they can to the
        context before requesting any from it. This will speed up the dependency
        resolution and prevent deadlocks.

        .. warning:: It's unadvisable to call this method directly (in case you're doing
            it in a test suite). Instead, call :func:`start_component`, as it comes with
            extra safeguards.
        """


class ContainerComponent(Component):
    """
    A component that can contain other components.

    :param components: dictionary of component alias ⭢ component configuration
        dictionary
    :ivar child_components: dictionary of component alias ⭢ :class:`Component` instance
        (of child components added with :meth:`add_component`)
    :vartype child_components: Dict[str, Component]
    :ivar component_configs: dictionary of component alias ⭢ externally provided
        component configuration
    :vartype component_configs: Dict[str, Optional[Dict[str, Any]]]
    """

    __slots__ = "child_components", "component_configs"

    def __init__(
        self, components: dict[str, dict[str, Any] | None] | None = None
    ) -> None:
        self.child_components: OrderedDict[str, Component] = OrderedDict()
        self.component_configs = components or {}

    def add_component(
        self, alias: str, type: str | type | None = None, **config: Any
    ) -> None:
        """
        Add a child component.

        This will instantiate a component class, as specified by the ``type`` argument.

        If the second argument is omitted, the value of ``alias`` is used as its value.

        The locally given configuration can be overridden by component configuration
        parameters supplied to the constructor (via the ``components`` argument).

        When configuration values are provided both as keyword arguments to this method
        and component configuration through the ``components`` constructor argument, the
        configurations are merged together using :func:`~asphalt.core.merge_config`
        in a way that the configuration values from the ``components`` argument override
        the keyword arguments to this method.

        :param alias: a name for the component instance, unique within this container
        :param type: name of and entry point in the ``asphalt.components`` namespace or
            a :class:`Component` subclass
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

    async def start(self) -> None:
        """
        Create child components that have been configured but not yet created and then
        calls their :meth:`~Component.start` methods in separate tasks and waits until
        they have completed.

        """
        for alias in self.component_configs:
            if alias not in self.child_components:
                self.add_component(alias)

        async with create_task_group() as tg:
            for alias, component in self.child_components.items():
                tg.start_soon(
                    component.start,
                    name=f"Starting {qualified_name(component)} ({alias})",
                )


class CLIApplicationComponent(ContainerComponent):
    """
    Specialized subclass of :class:`.ContainerComponent` for command line tools.

    Command line tools and similar applications should use this as their root component
    and implement their main code in the :meth:`run` method.

    When all the subcomponents have been started, :meth:`run` is started as a new task.
    When the task is finished, the application will exit using the return value as its
    exit code.

    If :meth:`run` raises an exception, a stack trace is printed and the exit code will
    be set to 1. If the returned exit code is out of range or of the wrong data type,
    it is set to 1 and a warning is emitted.
    """

    @abstractmethod
    async def run(self) -> int | None:
        """
        Run the business logic of the command line tool.

        Do not call this method yourself.

        :return: the application's exit code (0-127; ``None`` = 0)
        """


component_types = PluginContainer("asphalt.components", Component)


async def start_component(
    component: Component,
    *,
    start_timeout: float | None = 20,
    startup_scope: CancelScope | None = None,
) -> None:
    """
    Start a component and its subcomponents.

    :param component: the (root) component to start
    :param start_timeout: seconds to wait for all the components in the hierarchy to
        start (default: ``20``; set to ``None`` to disable timeout)
    :param startup_scope: used only by :func:`run_application`
    :raises RuntimeError: if this function is called without an active :class:`Context`

    """
    try:
        current_context()
    except NoCurrentContext:
        raise RuntimeError(
            "start_component() requires an active Asphalt context"
        ) from None

    with ExitStack() as exit_stack:
        if startup_scope is None:
            startup_scope = exit_stack.enter_context(CancelScope())

        startup_watcher_scope: CancelScope | None = None
        if start_timeout is not None:
            startup_watcher_scope = await start_service_task(
                lambda task_status: _component_startup_watcher(
                    startup_scope,
                    component,
                    start_timeout,
                    task_status=task_status,
                ),
                "Asphalt component startup watcher task",
            )

        await component.start()

        # Cancel the startup timeout, if any
        if startup_watcher_scope:
            startup_watcher_scope.cancel()


async def _component_startup_watcher(
    startup_cancel_scope: CancelScope,
    root_component: Component,
    start_timeout: float,
    *,
    task_status: TaskStatus[CancelScope],
) -> None:
    def get_coro_stack_summary(coro: Any) -> StackSummary:
        import gc

        frames: list[FrameType] = []
        while isinstance(coro, Coroutine):
            while coro.__class__.__name__ == "async_generator_asend":
                # Hack to get past asend() objects
                coro = gc.get_referents(coro)[0].ag_await

            if frame := getattr(coro, "cr_frame", None):
                frames.append(frame)

            coro = getattr(coro, "cr_await", None)

        frame_tuples = [(f, f.f_lineno) for f in frames]
        return StackSummary.extract(frame_tuples)

    current_task = get_current_task()
    parent_task = next(
        task_info
        for task_info in get_running_tasks()
        if task_info.id == current_task.parent_id
    )

    with CancelScope() as cancel_scope:
        task_status.started(cancel_scope)
        await sleep(start_timeout)

    if cancel_scope.cancel_called:
        return

    @dataclass
    class ComponentStatus:
        name: str
        alias: str | None
        parent_task_id: int | None
        traceback: list[str] = field(init=False, default_factory=list)
        children: list[ComponentStatus] = field(init=False, default_factory=list)

    import re
    import textwrap

    component_task_re = re.compile(r"^Starting (\S+) \((.+)\)$")
    component_statuses: dict[int, ComponentStatus] = {}
    for task in get_running_tasks():
        if task.id == parent_task.id:
            status = ComponentStatus(qualified_name(root_component), None, None)
        elif task.name and (match := component_task_re.match(task.name)):
            name: str
            alias: str
            name, alias = match.groups()
            status = ComponentStatus(name, alias, task.parent_id)
        else:
            continue

        status.traceback = get_coro_stack_summary(task.coro).format()
        component_statuses[task.id] = status

    root_status: ComponentStatus
    for task_id, component_status in component_statuses.items():
        if component_status.parent_task_id is None:
            root_status = component_status
        elif parent_status := component_statuses.get(component_status.parent_task_id):
            parent_status.children.append(component_status)
            if parent_status.alias:
                component_status.alias = (
                    f"{parent_status.alias}.{component_status.alias}"
                )

    def format_status(status_: ComponentStatus, level: int) -> str:
        title = f"{status_.alias or 'root'} ({status_.name})"
        if status_.children:
            children_output = ""
            for i, child in enumerate(status_.children):
                prefix = "| " if i < (len(status_.children) - 1) else "  "
                children_output += "+-" + textwrap.indent(
                    format_status(child, level + 1),
                    prefix,
                    lambda line: line[0] in " +|",
                )

            output = title + "\n" + children_output
        else:
            formatted_traceback = "".join(status_.traceback)
            if level == 0:
                formatted_traceback = textwrap.indent(formatted_traceback, "| ")

            output = title + "\n" + formatted_traceback

        return output

    getLogger(__name__).error(
        "Timeout waiting for the root component to start\n"
        "Components still waiting to finish startup:\n%s",
        textwrap.indent(format_status(root_status, 0).rstrip(), "  "),
    )
    startup_cancel_scope.cancel()
