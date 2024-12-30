from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from collections.abc import (
    Awaitable,
    Coroutine,
    Mapping,
    MutableMapping,
    Sequence,
)
from contextlib import AsyncExitStack
from enum import Enum, auto
from inspect import isclass
from traceback import StackSummary
from types import FrameType
from typing import (
    Any,
    Callable,
    ClassVar,
    Literal,
    TypeVar,
    get_type_hints,
    overload,
)

from anyio import create_task_group, sleep
from anyio.abc import TaskGroup

from ._concurrent import ExceptionHandler, TaskFactory, TeardownAction
from ._context import (
    Context,
    FactoryCallback,
    T_Resource,
    TeardownCallback,
    current_context,
)
from ._exceptions import ComponentStartError, NoCurrentContext, ResourceNotFound
from ._utils import (
    PluginContainer,
    coalesce_exceptions,
    format_component_name,
    merge_config,
    qualified_name,
)

logger = logging.getLogger("asphalt.core")

TComponent = TypeVar("TComponent", bound="Component")
T_Retval = TypeVar("T_Retval")


class Component(metaclass=ABCMeta):
    """This is the base class for all Asphalt components."""

    _isolated: ClassVar[bool]

    _child_components: dict[str, dict[str, Any]] | None = None
    _component_started = False

    def __init_subclass__(cls, *, isolated: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._isolated = isolated

    def add_component(
        self, alias: str, /, type: str | type[Component] | None = None, **config: Any
    ) -> None:
        """
        Add a child component.

        This will store the type and configuration options of the named child component,
        to be later instantiated by :func:`start_component`.

        If the ``type`` argument is omitted, then the value of the ``alias`` argument is
        used to derive the type.

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
        :param config: mapping of keyword arguments passed to the component's
            initializer
        :raises RuntimeError: if there is already a child component with the same alias

        """
        if self._component_started:
            raise RuntimeError(
                "child components cannot be added once start_component() has been "
                "called on the component"
            )

        if not isinstance(alias, str) or not alias:
            raise TypeError("alias must be a nonempty string")

        if self._child_components is None:
            self._child_components = {}
        elif alias in self._child_components:
            raise ValueError(f'there is already a child component named "{alias}"')

        self._child_components[alias] = {"type": type or alias, **config}

    async def prepare(self) -> None:
        """
        Perform any necessary initialization before starting the component.

        This method is called by :func:`start_component` *before* starting the child
        components of this component, so it can be used to add any resources required
        by the child components.
        """

    async def start(self) -> None:
        """
        Perform any necessary tasks to start the services provided by this component.

        This method is called by :func:`start_component` *after* the child components of
        this component have been started, so any resources provided by the child
        components are available at this point.

        .. note:: Resources added within this method with the default name may be
            published under a different name if the component is deployed as a child
            component with an alias like ``componenttype/resourcename``. In this
            case, resources added with the default name will be published under the name
            ``resourcename`` instead of ``default``.

        .. warning:: Do not call this method directly; use :func:`start_component`
            instead.
        """


class CLIApplicationComponent(Component):
    """
    Specialized :class:`.Component` subclass for command line tools.

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


class ComponentState(Enum):
    initialized = auto()
    preparing = auto()
    starting_children = auto()
    starting = auto()
    started = auto()
    closed = auto()


class ComponentContext(Context):
    def __init__(
        self,
        component: Component,
        path: str,
        default_resource_name: str,
        child_component_contexts: dict[str, ComponentContext],
    ):
        super().__init__()
        self.path = path
        self._component = component
        self._default_resource_name = default_resource_name
        self._child_component_contexts = child_component_contexts
        self._component_state: ComponentState = ComponentState.initialized
        self._coro: Coroutine[Any, Any, None] | None = None
        context = current_context()
        if isinstance(context, ComponentContext):
            context = context._context

        self._context: Context = context

    @staticmethod
    def _format_resource_description(
        types: Any, name: str, description: str | None = None
    ) -> str:
        if isinstance(types, Sequence):
            formatted_types = ", ".join(qualified_name(type_) for type_ in types)
            formatted = f"types=[{formatted_types}]"
        else:
            formatted = f"type={qualified_name(types)}"

        formatted += f", name={name!r}"
        if description:
            formatted += f", description={description!r}"

        return formatted

    def add_resource(
        self,
        value: T_Resource,
        name: str = "default",
        types: type | Sequence[type] = (),
        *,
        description: str | None = None,
        teardown_callback: Callable[[], Any] | None = None,
    ) -> None:
        if name == "default" and self._component_state is ComponentState.starting:
            name = self._default_resource_name

        self._context.add_resource(
            value,
            name,
            types,
            description=description,
            teardown_callback=teardown_callback,
        )
        logger.debug(
            "%s added a resource (%s)",
            format_component_name(self.path, capitalize=True),
            self._format_resource_description(types or type(value), name, description),
        )

    def add_resource_factory(
        self,
        factory_callback: FactoryCallback,
        name: str = "default",
        *,
        types: type | Sequence[type] = (),
        description: str | None = None,
    ) -> None:
        if name == "default" and self._component_state is ComponentState.starting:
            name = self._default_resource_name

        self._context.add_resource_factory(
            factory_callback, name, types=types, description=description
        )
        logger.debug(
            "%s added a resource factory (%s)",
            format_component_name(self.path, capitalize=True),
            self._format_resource_description(
                types or get_type_hints(factory_callback)["return"], name, description
            ),
        )

    @overload
    async def get_resource(
        self,
        type: type[T_Resource],
        name: str = ...,
        *,
        optional: Literal[True],
    ) -> T_Resource | None: ...

    @overload
    async def get_resource(
        self,
        type: type[T_Resource],
        name: str = ...,
        *,
        optional: Literal[False],
    ) -> T_Resource: ...

    @overload
    async def get_resource(
        self, type: type[T_Resource], name: str = ...
    ) -> T_Resource: ...

    async def get_resource(
        self,
        type: type[T_Resource],
        name: str = "default",
        *,
        optional: Literal[False, True] = False,
    ) -> T_Resource | None:
        if optional:
            return await self._context.get_resource(type, name, optional=True)

        try:
            return await self._context.get_resource(type, name)
        except ResourceNotFound:
            logger.debug(
                "%s is waiting for another component to provide a resource (%s)",
                format_component_name(self.path, capitalize=True),
                self._format_resource_description(type, name),
            )

            # Wait until a matching resource or resource factory is available
            await self._context.resource_added.wait_event(
                lambda event: event.resource_name == name
                and type in event.resource_types,
            )
            res = await self._context.get_resource(type, name)
            logger.debug(
                "%s got the resource it was waiting for (%s)",
                format_component_name(self.path, capitalize=True),
                self._format_resource_description(type, name),
            )
            return res

    @overload
    def get_resource_nowait(
        self, type: type[T_Resource], name: str = ..., *, optional: Literal[True]
    ) -> T_Resource | None: ...

    @overload
    def get_resource_nowait(
        self, type: type[T_Resource], name: str = ..., *, optional: Literal[False]
    ) -> T_Resource: ...

    @overload
    def get_resource_nowait(
        self, type: type[T_Resource], name: str = ...
    ) -> T_Resource: ...

    def get_resource_nowait(
        self,
        type: type[T_Resource],
        name: str = "default",
        *,
        optional: Literal[False, True] = False,
    ) -> T_Resource | None:
        if optional:
            return self._context.get_resource_nowait(type, name, optional=True)

        return self._context.get_resource_nowait(type, name)

    def get_resources(self, type: type[T_Resource]) -> Mapping[str, T_Resource]:
        return self._context.get_resources(type)

    def add_teardown_callback(
        self, callback: TeardownCallback, pass_exception: bool = False
    ) -> None:
        self._context.add_teardown_callback(callback, pass_exception)

    async def start_background_task_factory(
        self, *, exception_handler: ExceptionHandler | None = None
    ) -> TaskFactory:
        factory = await self._context.start_background_task_factory(
            exception_handler=exception_handler
        )
        logger.debug(
            "%s started a background task factory",
            format_component_name(self.path, capitalize=True),
        )
        return factory

    async def start_service_task(
        self,
        func: Callable[..., Coroutine[Any, Any, T_Retval]],
        name: str,
        *,
        teardown_action: TeardownAction = "cancel",
    ) -> Any:
        retval = await self._context.start_service_task(
            func, name, teardown_action=teardown_action
        )
        logger.debug(
            "%s started a service task (%s)",
            format_component_name(self.path, capitalize=True),
            name,
        )
        return retval


@overload
async def start_component(
    component_class: type[TComponent],
    config: dict[str, Any] | None = ...,
    *,
    timeout: float | None = ...,
) -> TComponent: ...


@overload
async def start_component(
    component_class: str,
    config: dict[str, Any] | None = ...,
    *,
    timeout: float | None = ...,
) -> Component: ...


async def start_component(
    component_class: type[Component] | str,
    config: dict[str, Any] | None = None,
    *,
    timeout: float | None = 20,
) -> Component:
    """
    Start a component and its subcomponents.

    :param component_class: the root component class, an entry point name in the
        ``asphalt.components`` namespace or a ``modulename:varname`` reference
    :param config: configuration for the root component (and its child components)
    :param timeout: seconds to wait for all the components in the hierarchy to start
        (default: ``20``; set to ``None`` to disable timeout)
    :raises RuntimeError: if this function is called without an active :class:`Context`
    :raises TimeoutError: if the startup of the component hierarchy takes more than
        ``timeout`` seconds
    :raises TypeError: if ``component_class`` is neither a :class:`Component` subclass
        or a string

    """
    try:
        current_context()
    except NoCurrentContext:
        raise RuntimeError(
            "start_component() requires an active Asphalt context"
        ) from None

    if config is None:
        config = {}
    elif not isinstance(config, MutableMapping):
        raise TypeError("config must be a dict (or any other mutable mapping) or None")

    root_component_context = _init_component("", {"type": component_class, **config})
    async with AsyncExitStack() as exit_stack:
        tg: TaskGroup | None = None
        if timeout:
            await exit_stack.enter_async_context(coalesce_exceptions())
            tg = await exit_stack.enter_async_context(create_task_group())
            tg.start_soon(
                _watch_component_tree_startup,
                root_component_context,
                timeout,
            )

        await _start_component(root_component_context)

        if tg:
            tg.cancel_scope.cancel()

    return root_component_context._component


def _init_component(
    path: str,
    config: MutableMapping[str, Any],
    default_resource_name: str = "default",
) -> ComponentContext:
    # Separate the child components from the config
    child_components_config = config.pop("components", {})

    # Resolve the type to a class
    component_type = config.pop("type")
    component_class = component_types.resolve(component_type)
    if not isclass(component_class) or not issubclass(component_class, Component):
        raise TypeError(
            f"{path or '(root)'}: the declared component type ({component_type!r}) "
            f"resolved to {component_class!r} which is not a subclass of Component"
        )

    # Instantiate the component
    logger.debug("Creating %s", format_component_name(path, component_class))
    try:
        component = component_class(**config)
    except Exception as exc:
        raise ComponentStartError("creating", path, component_class) from exc

    # Merge the overrides to the hard-coded configuration
    logger.debug("Created %s", format_component_name(path, component_class))
    child_components_config = merge_config(
        component._child_components, child_components_config
    )

    # Create the child components
    child_contexts: dict[str, ComponentContext] = {}
    for alias, child_config in child_components_config.items():
        child_path = f"{path}.{alias}" if path else alias

        if child_config is None:
            child_config = {}
        elif not isinstance(child_config, MutableMapping):
            raise TypeError(
                f"{child_path}: component configuration must be either None or a "
                f"dict (or any other mutable mapping type), not "
                f"{qualified_name(child_config)}"
            )

        # If the type was specified only via an alias, use that as a type
        child_config.setdefault("type", alias)

        # If the type contains a forward slash, split the latter part out of it
        if isinstance(child_config["type"], str) and "/" in child_config["type"]:
            child_config["type"] = child_config["type"].split("/")[0]

        if "/" in alias:
            child_default_resource_name = alias.split("/", 1)[1]
        else:
            child_default_resource_name = "default"

        child_contexts[child_path] = _init_component(
            child_path, child_config, child_default_resource_name
        )

    return ComponentContext(component, path, default_resource_name, child_contexts)


async def _start_component(context: ComponentContext) -> None:
    # Prevent add_component() from being called beyond this point
    component = context._component
    component._component_started = True
    component_class = type(component)

    async with context:
        # Call prepare() on the component itself, if it's implemented on the component
        # class
        if component_class.prepare is not Component.prepare:
            logger.debug("Calling prepare() of %s", format_component_name(context.path))
            context._component_state = ComponentState.preparing
            coro = context._coro = component.prepare()
            try:
                await coro
            except Exception as exc:
                raise ComponentStartError(
                    "preparing", context.path, component_class
                ) from exc

            logger.debug(
                "Returned from prepare() of %s", format_component_name(context.path)
            )
            context._coro = None

        # Start the child components, if there are any
        if context._child_component_contexts:
            logger.debug(
                "Starting the child components of %s",
                format_component_name(context.path),
            )
            context._component_state = ComponentState.starting_children
            async with coalesce_exceptions(), create_task_group() as tg:
                for alias, child_context in context._child_component_contexts.items():
                    tg.start_soon(
                        _start_component,
                        child_context,
                        name=(
                            f"Starting component {child_context.path} "
                            f"({qualified_name(child_context._component)})"
                        ),
                    )

        # Call start() on the component itself, if it's implemented on the component
        # class
        if component_class.start is not Component.start:
            context._component_state = ComponentState.starting
            logger.debug("Calling start() of %s", format_component_name(context.path))
            coro = context._coro = component.start()
            context._component_state = ComponentState.starting
            try:
                await coro
            except Exception as exc:
                raise ComponentStartError(
                    "starting", context.path, component_class
                ) from exc

            logger.debug(
                "Returned from start() of %s", format_component_name(context.path)
            )
            context._coro = None

        context._component_state = ComponentState.started


async def _watch_component_tree_startup(
    context: ComponentContext,
    timeout: float,
) -> None:
    def create_status_summaries(subcontext: ComponentContext) -> list[str]:
        parts = (subcontext.path or "(root)").split(".")
        indent = "  " * (len(parts) if subcontext.path else 0)
        state = subcontext._component_state.name.replace("_", " ")
        summaries = [f"{indent}{parts[-1]}: {state}"]
        for child_context in subcontext._child_component_contexts.values():
            if child_context._component_state is not ComponentState.started:
                summaries.extend(create_status_summaries(child_context))

        return summaries

    def create_stack_summaries(subcontext: ComponentContext) -> list[str]:
        summaries: list[str] = []
        if subcontext._coro is not None:
            stack_summary = _get_coro_stack_summary(subcontext._coro)
            formatted_summary = "".join(stack_summary.format())
            title = f"{subcontext.path} ({qualified_name(subcontext._component)})"
            summaries.append(f"{title}:\n{formatted_summary.rstrip()}")

        for child_context in subcontext._child_component_contexts.values():
            summaries.extend(create_stack_summaries(child_context))

        return summaries

    await sleep(timeout)
    status_summary_sections: list[str] = [
        "Timeout waiting for the component tree to start"
    ]
    status_summaries: list[str] = create_status_summaries(context)
    title = "Current status of the components still waiting to finish startup"
    status_summary_sections.append(f"{title}\n{'-' * len(title)}")
    status_summary_sections.append("\n".join(status_summaries))

    if stack_summaries := create_stack_summaries(context):
        title = "Stack summaries of components still waiting to start"
        status_summary_sections.append(f"{title}\n{'-' * len(title)}")
        status_summary_sections.extend(stack_summaries)

    logger.error("%s", "\n\n".join(status_summary_sections))
    raise TimeoutError("timeout starting component tree")


def _get_coro_stack_summary(coro: Coroutine[Any, Any, Any]) -> StackSummary:
    import gc

    frames: list[FrameType] = []
    awaitable: Awaitable[Any] | None = coro
    while isinstance(awaitable, Coroutine):
        while awaitable.__class__.__name__ == "async_generator_asend":
            # Hack to get past asend() objects
            awaitable = gc.get_referents(awaitable)[0].ag_await

        if frame := getattr(awaitable, "cr_frame", None):
            frames.append(frame)

        awaitable = getattr(awaitable, "cr_await", None)

    frame_tuples = [(f, f.f_lineno) for f in frames]
    return StackSummary.extract(frame_tuples)
