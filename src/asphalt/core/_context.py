from __future__ import annotations

import logging
import re
import sys
import types
import warnings
from collections.abc import AsyncGenerator, Coroutine, Sequence
from contextlib import AsyncExitStack
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import wraps
from inspect import (
    Parameter,
    isasyncgenfunction,
    isawaitable,
    isclass,
    iscoroutinefunction,
    signature,
)
from types import TracebackType
from typing import (
    Any,
    Callable,
    Generic,
    Literal,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

import anyio
from anyio import CancelScope, create_task_group, get_current_task
from anyio.abc import TaskGroup

from ._event import Event, Signal, wait_event
from ._exceptions import ApplicationExit
from ._utils import callable_name, qualified_name

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from exceptiongroup import BaseExceptionGroup
    from typing_extensions import Self

logger = logging.getLogger(__name__)
factory_callback_type = Callable[["Context"], Any]
resource_name_re = re.compile(r"\w+")
T_Resource = TypeVar("T_Resource")
T_Retval = TypeVar("T_Retval")
T_Context = TypeVar("T_Context", bound="Context")
T_Self = TypeVar("T_Self")
P = ParamSpec("P")
_current_context: ContextVar[Context | None] = ContextVar(
    "_current_context", default=None
)


@dataclass(frozen=True)
class ResourceContainer:
    """
    Contains the resource value or its factory callable, plus some metadata.

    :ivar value_or_factory: the resource value or the factory callback
    :ivar types: type names the resource was registered with
    :vartype types: Tuple[type, ...]
    :ivar str name: name of the resource
    :ivar description: free-form description of the resource
    :ivar bool is_factory: ``True`` if ``value_or_factory`` if this is a resource
        factory
    """

    value_or_factory: Any
    types: tuple[type, ...]
    name: str
    description: str | None
    is_factory: bool


@dataclass
class ResourceEvent(Event):
    """
    Dispatched when a resource or resource factory has been added to a context.

    :ivar resource_types: types the resource was registered under
    :vartype resource_types: tuple[type, ...]
    :ivar str name: name of the resource
    :ivar bool is_factory: ``True`` if a resource factory was added, ``False`` if a
        regular resource was added
    """

    resource_types: tuple[type, ...]
    resource_name: str
    is_factory: bool


@dataclass(frozen=True)
class GeneratedResource(Generic[T_Resource]):
    resource: T_Resource
    teardown_callback: Callable[[], None | Coroutine] | None


class AwaitableResourceError(Exception):
    """
    Raised when a resource was requested that comes from an asynchronous factory, but
    the caller was in a synchronous async callback.
    """


class ResourceConflict(Exception):
    """
    Raised when a new resource that is being published conflicts with an existing
    resource or context variable.
    """


class ResourceNotFound(LookupError):
    """Raised when a resource request cannot be fulfilled within the allotted time."""

    def __init__(self, type: type, name: str) -> None:
        super().__init__(type, name)
        self.type = type
        self.name = name

    def __str__(self):
        return (
            f"no matching resource was found for type={qualified_name(self.type)} "
            f"name={self.name!r}"
        )


class NoCurrentContext(Exception):
    """Raised by :func: `current_context` when there is no active context."""

    def __init__(self) -> None:
        super().__init__("There is no active context")


class ContextState(Enum):
    inactive = auto()
    open = auto()
    closing = auto()
    closed = auto()


class Context:
    """
    Contexts give request handlers and callbacks access to resources.

    Contexts are stacked in a way that accessing an attribute that is not present in the
    current context causes the attribute to be looked up in the parent instance and so
    on, until the attribute is found (or :class:`AttributeError` is raised).

    :var Signal resource_added: a signal (:class:`ResourceEvent`) dispatched when a
        resource has been published in this context
    """

    resource_added = Signal(ResourceEvent)

    _task_group: TaskGroup
    _reset_token: Token
    _exit_stack: AsyncExitStack

    def __init__(self) -> None:
        self._parent = _current_context.get(None)
        self._state = ContextState.inactive
        self._resources: dict[tuple[type, str], ResourceContainer] = {}
        self._resource_factories: dict[tuple[type, str], ResourceContainer] = {}
        self._teardown_callbacks: list[tuple[Callable, bool]] = []

    @property
    def context_chain(self) -> list[Context]:
        """Return a list of contexts starting from this one, its parent and so on."""
        contexts = []
        ctx: Context | None = self
        while ctx is not None:
            contexts.append(ctx)
            ctx = ctx.parent

        return contexts

    @property
    def parent(self) -> Context | None:
        """Return the parent context, or ``None`` if there is no parent."""
        return self._parent

    @property
    def closed(self) -> bool:
        """
        Return ``True`` if the teardown process has at least been initiated, ``False``
        otherwise.

        """
        return self._state in (ContextState.closing, ContextState.closed)

    def _ensure_state(self, *allowed_states: ContextState) -> None:
        if self._state in allowed_states:
            return

        if self._state is ContextState.inactive:
            raise RuntimeError("this context has not been entered yet")
        elif self._state is ContextState.open:
            raise RuntimeError("this context has already been entered")
        elif self._state is ContextState.closed:
            raise RuntimeError("this context has already been closed")
        else:
            assert self._state is ContextState.closing
            raise RuntimeError("this context is being torn down")

    async def _run_teardown_callbacks(self) -> None:
        original_exception = sys.exc_info()[1]
        exceptions: list[BaseException] = []
        while self._teardown_callbacks:
            callback, pass_exception = self._teardown_callbacks.pop()
            try:
                if pass_exception:
                    retval = callback(original_exception)
                else:
                    retval = callback()

                if isawaitable(retval):
                    await retval
            except BaseException as e:
                exceptions.append(e)

        if exceptions:
            excgrp = BaseExceptionGroup(
                "Exceptions were raised during context teardown", exceptions
            )
            del exceptions
            raise excgrp from original_exception

    def add_teardown_callback(
        self, callback: Callable, pass_exception: bool = False
    ) -> None:
        """
        Add a callback to be called when this context closes.

        This is intended for cleanup of resources, and the list of callbacks is
        processed in the reverse order in which they were added, so the last added
        callback will be called first.

        The callback may return an awaitable. If it does, the awaitable is awaited on
        before calling any further callbacks.

        :param callback: a callable that is called with either no arguments or with the
            exception that ended this context, based on the value of ``pass_exception``
        :param pass_exception: ``True`` to pass the callback the exception that ended
            this context (or ``None`` if the context ended cleanly)

        """
        self._ensure_state(ContextState.open, ContextState.closing)
        if not callable(callback):
            raise TypeError("callback must be a callable")

        self._teardown_callbacks.append((callback, pass_exception))

    async def __aenter__(self) -> Self:
        self._ensure_state(ContextState.inactive)
        self._state = ContextState.open
        try:
            async with AsyncExitStack() as exit_stack:
                self._host_task = get_current_task()
                exit_stack.callback(delattr, self, "_host_task")
                _reset_token = _current_context.set(self)
                exit_stack.callback(_current_context.reset, _reset_token)
                self._task_group = await exit_stack.enter_async_context(
                    create_task_group()
                )
                exit_stack.push_async_callback(self._run_teardown_callbacks)
                self._exit_stack = exit_stack.pop_all()
        except BaseException:
            self._state = ContextState.inactive
            raise

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._state = ContextState.closing
        try:
            await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            self._state = ContextState.closed

    async def add_resource(
        self,
        value: T_Resource,
        name: str = "default",
        types: type | Sequence[type] = (),
        *,
        description: str | None = None,
        teardown_callback: Callable[[], Any] | None = None,
    ) -> None:
        """
        Add a resource to this context.

        This will cause a ``resource_added`` event to be dispatched.

        :param value: the actual resource value
        :param name: name of this resource (unique among all its registered types within
            a single context)
        :param types: type(s) to register the resource as (omit to use the type of
            ``value``)
        :param description: an optional free-form description, for
            introspection/debugging
        :param teardown_callback: callable that is called to perform cleanup on this
            resource when the context is being shut down
        :raises asphalt.core.context.ResourceConflict: if the resource conflicts with an
            existing one in any way

        """
        self._ensure_state(ContextState.open, ContextState.closing)
        types_: tuple[type, ...]
        if types:
            if (
                isclass(types)
                or get_origin(types) is not None
                or not isinstance(types, Sequence)
            ):
                types_ = (cast(type, types),)
            else:
                types_ = tuple(types)

            if not all(isclass(x) or get_origin(x) is not None for x in types_):
                raise TypeError("types must be a type or sequence of types")
        else:
            types_ = (type(value),)

        if value is None:
            raise ValueError('"value" must not be None')

        if not resource_name_re.fullmatch(name):
            raise ValueError(
                '"name" must be a nonempty string consisting only of alphanumeric '
                "characters and underscores"
            )

        for resource_type in types_:
            if (resource_type, name) in self._resources:
                raise ResourceConflict(
                    f"this context already contains a resource of type "
                    f"{qualified_name(resource_type)} using the name {name!r}"
                )

        container = ResourceContainer(value, types_, name, description, False)
        for type_ in types_:
            self._resources[(type_, name)] = container

        # Add the teardown callback, if any
        if teardown_callback is not None:
            self.add_teardown_callback(teardown_callback)

        # Notify listeners that a new resource has been made available
        await self.resource_added.dispatch(ResourceEvent(types_, name, False))

    async def add_resource_factory(
        self,
        factory_callback: factory_callback_type,
        name: str = "default",
        *,
        types: Sequence[type] | None = None,
        description: str | None = None,
    ) -> None:
        """
        Add a resource factory to this context.

        This will cause a ``resource_added`` event to be dispatched.

        A resource factory is a callable that generates a "contextual" resource when it
        is requested by either using any of the methods :meth:`get_resource`,
        :meth:`require_resource` or :meth:`request_resource` or its context attribute is
        accessed.

        The type(s) of the generated resources need to be specified, either by passing
        the ``types`` argument, or by adding a return type annotation to the factory
        function. If the generated resource needs to be registered as multiple types,
        you can use :data:`~typing.Union` (e.g. ``Union[str, int]``) or a union type
        (e.g. ``str | int``; requires ``from __future__ import annotations`` on earlier
        than Python 3.10).

        When a new resource is created in this manner, it is always bound to the context
        through it was requested, regardless of where in the chain the factory itself
        was added to.

        :param factory_callback: a (non-coroutine) callable that takes a context
            instance as argument and returns the created resource object
        :param name: name of the resource that will be created in the target context
        :param types: one or more types to register the generated resource as on the
            target context (can be omitted if the factory callable has a return type
            annotation)
        :param description: an optional free-form description, for
            introspection/debugging
        :raises asphalt.core.context.ResourceConflict: if there is an existing resource
            factory for the given type/name combinations or the given context variable

        """
        import types as stdlib_types

        self._ensure_state(ContextState.open)
        if not resource_name_re.fullmatch(name):
            raise ValueError(
                '"name" must be a nonempty string consisting only of alphanumeric '
                "characters and underscores"
            )

        if types is not None:
            if isinstance(types, type):
                resource_types: tuple[type, ...] = (types,)
            else:
                resource_types = tuple(types)
        else:
            # Extract the resources types from the return type annotation of the factory
            type_hints = get_type_hints(factory_callback)
            try:
                return_type_hint = type_hints["return"]
            except KeyError:
                raise ValueError(
                    "no resource types specified, and the factory callback does not "
                    "have a return type hint"
                )

            origin = get_origin(return_type_hint)
            if origin is GeneratedResource:
                args = get_args(return_type_hint)
                if len(args) != 1:
                    raise ValueError(
                        f"GeneratedResource must specify exactly one parameter, got "
                        f"{len(args)}"
                    )

                return_type_hint = args[0]

            if origin is Union or (
                sys.version_info >= (3, 10) and origin is stdlib_types.UnionType
            ):
                resource_types = get_args(return_type_hint)
            else:
                resource_types = (return_type_hint,)

        if not resource_types:
            raise ValueError("no resource types were specified")

        if None in resource_types:
            raise TypeError("None is not a valid resource type")

        # Check for conflicts with existing resource factories
        for type_ in resource_types:
            if (type_, name) in self._resource_factories:
                raise ResourceConflict(
                    f"this context already contains a resource factory for the "
                    f"type {qualified_name(type_)}"
                )

        # Add the resource factory to the appropriate lookup tables
        resource = ResourceContainer(
            factory_callback, resource_types, name, description, True
        )
        for type_ in resource_types:
            self._resource_factories[(type_, name)] = resource

        # Notify listeners that a new resource has been made available
        await self.resource_added.dispatch(ResourceEvent(resource_types, name, True))

    def _generate_resource_from_factory(self, factory: ResourceContainer) -> Any:
        retval = factory.value_or_factory(self)
        if isinstance(retval, GeneratedResource):
            resource = retval.resource
            if retval.teardown_callback is not None:
                print("Adding teardown callback to context", hex(id(self)))
                self.add_teardown_callback(retval.teardown_callback)
        else:
            resource = retval

        container = ResourceContainer(
            resource, factory.types, factory.name, factory.description, False
        )
        for type_ in factory.types:
            self._resources[(type_, factory.name)] = container

        return resource

    def get_static_resources(self, type: type[T_Resource]) -> set[T_Resource]:
        """
        Retrieve all the (static) resources of the given type in this context and its
        parents.

        This will **not** use resource factories to generate matching resources.

        :param type: resource type to filter by
        :return: a set of matching resource containers

        """
        # Collect all the matching resources from this context
        containers = {
            container
            for ctx in self.context_chain
            for container in ctx._resources.values()
            if type in container.types and not container.is_factory
        }
        return {container.value_or_factory for container in containers}

    def get_resource(
        self, type: type[T_Resource], name: str = "default"
    ) -> T_Resource | None:
        """
        Look up a resource in the chain of contexts.

        :param type: type of the requested resource
        :param name: name of the requested resource
        :return: the requested resource, or ``None`` if none was available

        """
        self._ensure_state(ContextState.open, ContextState.closing)
        key = (type, name)

        # First check if there's already a matching resource in this context
        resource = self._resources.get(key)
        if resource is not None:
            return resource.value_or_factory

        # Next, check if there's a resource factory available on the context chain
        factory = next(
            (
                ctx._resource_factories[key]
                for ctx in self.context_chain
                if key in ctx._resource_factories
            ),
            None,
        )
        if factory is not None:
            return self._generate_resource_from_factory(factory)

        # Finally, check parents for a matching resource
        return next(
            (
                ctx._resources[key].value_or_factory
                for ctx in self.context_chain
                if key in ctx._resources
            ),
            None,
        )

    def get_resources(self, type: type[T_Resource]) -> set[T_Resource]:
        """
        Retrieve all the resources of the given type in this context and its parents.

        Any matching resource factories are also triggered if necessary.

        :param type: type of the resources to get
        :return: a set of all found resources of the given type

        """
        # Collect all the matching resources from this context
        resources: dict[str, T_Resource] = {
            container.name: container.value_or_factory
            for container in self._resources.values()
            if not container.is_factory and type in container.types
        }

        # Next, find all matching resource factories in the context chain and generate
        # resources
        resources.update(
            {
                container.name: self._generate_resource_from_factory(container)
                for ctx in self.context_chain
                for container in ctx._resources.values()
                if container.is_factory
                and type in container.types
                and container.name not in resources
            }
        )

        # Finally, add the resource values from the parent contexts
        resources.update(
            {
                container.name: container.value_or_factory
                for ctx in self.context_chain[1:]
                for container in ctx._resources.values()
                if not container.is_factory
                and type in container.types
                and container.name not in resources
            }
        )

        return set(resources.values())

    def require_resource(
        self, type: type[T_Resource], name: str = "default"
    ) -> T_Resource:
        """
        Look up a resource in the chain of contexts and raise an exception if it is not
        found.

        This is like :meth:`get_resource` except that instead of returning ``None`` when
        a resource is not found, it will raise
        :exc:`~asphalt.core.context.ResourceNotFound`.

        :param type: type of the requested resource
        :param name: name of the requested resource
        :return: the requested resource
        :raises asphalt.core.context.ResourceNotFound: if a resource of the given type
            and name was not found

        """
        resource = self.get_resource(type, name)
        if resource is None:
            raise ResourceNotFound(type, name)

        return resource

    async def request_resource(
        self, type: type[T_Resource], name: str = "default"
    ) -> T_Resource:
        """
        Look up a resource in the chain of contexts.

        This is like :meth:`get_resource` except that if the resource is not already
        available, it will wait for one to become available.

        :param type: type of the requested resource
        :param name: name of the requested resource
        :return: the requested resource

        """
        # First try to locate an existing resource in this context and its parents
        value = self.get_resource(type, name)
        if value is not None:
            return value

        # Wait until a matching resource or resource factory is available
        signals = [ctx.resource_added for ctx in self.context_chain]
        await wait_event(
            signals,
            lambda event: event.resource_name == name and type in event.resource_types,
        )
        return self.require_resource(type, name)

    async def start_background_task(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        name: str,
        *,
        teardown_action: Callable[[], Any] | None | Literal["cancel"] = "cancel",
    ) -> None:
        """
        Start a task that runs independently on the background.

        The task runs in its own context, inherited from the root context.
        If the task raises an exception (inherited from :exc:`Exception`), it is logged
        with a descriptive message containing the task's name.

        To pass arguments to the target callable, pass them via lambda (e.g.
        ``lambda: yourfunc(arg1, arg2, kw=val)``)

        :param func: the coroutine function to run
        :param name: descriptive name for the task
        :param teardown_action: the action to take when the context is being shut down:
            ``None`` means no action, ``'cancel'`` means cancel the task, or you can
            supply a callback that is called instead

        """
        finished_event = anyio.Event()
        cancel_scope = CancelScope()

        async def run_background_task() -> None:
            nonlocal cancel_scope
            __tracebackhide__ = True  # trick supported by certain debugger frameworks
            logger.debug("Background task (%s) starting", name)
            with cancel_scope:
                async with Context():
                    try:
                        await func()
                    except ApplicationExit:
                        logger.debug(
                            "Background task (%s) triggered application exit", name
                        )
                        raise
                    except Exception:
                        logger.exception("Background task (%s) crashed", name)
                        raise
                    else:
                        logger.debug("Background task (%s) finished", name)
                    finally:
                        finished_event.set()

        async def finalize_task() -> None:
            if teardown_action == "cancel":
                logger.debug("Cancelling background task (%s)", name)
                cancel_scope.cancel()
            elif teardown_action:
                logger.debug(
                    "Calling teardown callback (%s) for background task (%s)",
                    callable_name(teardown_action),
                    name,
                )
                retval = teardown_action()
                if isawaitable(retval):
                    await retval

            logger.debug("Waiting for background task (%s) to finish", name)
            await finished_event.wait()

        # await self._task_group.start(run_background_task)
        self._task_group.start_soon(run_background_task)
        self._exit_stack.push_async_callback(finalize_task)


@overload
def context_teardown(
    func: Callable[[T_Context], AsyncGenerator[None, Exception | None]],
) -> Callable[[T_Context], Coroutine[Any, Any, None]]:
    ...


@overload
def context_teardown(
    func: Callable[[T_Self, T_Context], AsyncGenerator[None, Exception | None]],
) -> Callable[[T_Self, T_Context], Coroutine[Any, Any, None]]:
    ...


def context_teardown(
    func: Callable[[T_Context], AsyncGenerator[None, Exception | None]]
    | Callable[[T_Self, T_Context], AsyncGenerator[None, Exception | None]],
) -> (
    Callable[[T_Context], Coroutine[Any, Any, None]]
    | Callable[[T_Self, T_Context], Coroutine[Any, Any, None]]
):
    """
    Wrap an async generator function to execute the rest of the function at context
    teardown.

    This function returns an async function, which, when called, starts the wrapped
    async generator. The wrapped async function is run until the first ``yield``
    statement When the context is being torn down, the exception that ended the context,
    if any, is sent to the generator.

    For example::

        class SomeComponent(Component):
            @context_teardown
            async def start(self, ctx: Context):
                service = SomeService()
                ctx.add_resource(service)
                exception = yield
                service.stop()

    :param func: an async generator function
    :return: an async function

    """

    @wraps(func)
    async def wrapper(*args, **kwargs) -> None:
        async def teardown_callback(exception: Exception | None) -> None:
            try:
                await generator.asend(exception)
            except StopAsyncIteration:
                pass
            finally:
                await generator.aclose()

        try:
            ctx = next(arg for arg in args[:2] if isinstance(arg, Context))
        except StopIteration:
            raise RuntimeError(
                f"the first positional argument to {callable_name(func)}() has to be "
                f"a Context instance"
            ) from None

        generator = func(*args, **kwargs)
        try:
            await generator.asend(None)
        except StopAsyncIteration:
            pass
        except BaseException:
            await generator.aclose()
            raise
        else:
            ctx.add_teardown_callback(teardown_callback, True)

    if not isasyncgenfunction(func):
        raise TypeError(f"{callable_name(func)} must be an async generator function")

    return wrapper


def current_context() -> Context:
    """
    Return the currently active context.

    :raises NoCurrentContext: if there is no active context

    """
    ctx = _current_context.get()
    if ctx is None:
        raise NoCurrentContext

    return ctx


def get_resources(type: type[T_Resource]) -> set[T_Resource]:
    """Shortcut for ``current_context().get_resources(...)``."""
    return current_context().get_resources(type)


def get_resource(type: type[T_Resource], name: str = "default") -> T_Resource | None:
    """Shortcut for ``current_context().get_resource(...)``."""
    return current_context().get_resource(type, name)


def require_resource(type: type[T_Resource], name: str = "default") -> T_Resource:
    """Shortcut for ``current_context().require_resource(...)``."""
    return current_context().require_resource(type, name)


async def start_background_task(
    func: Callable[..., Coroutine[Any, Any, Any]],
    name: str,
    *,
    teardown_action: Callable[[], Any] | None | Literal["cancel"] = "cancel",
) -> None:
    """Shortcut for ``current_context().start_background_task(...)``."""
    await current_context().start_background_task(
        func, name, teardown_action=teardown_action
    )


@dataclass
class _Dependency:
    name: str = "default"
    cls: type = field(init=False, repr=False)
    optional: bool = field(init=False, default=False)

    def __getattr__(self, item):
        raise AttributeError(
            "Attempted to access an attribute in a resource() marker – did you forget "
            "to add the @inject decorator?"
        )


def resource(name: str = "default") -> Any:
    """
    Marker for declaring a parameter for dependency injection via :func:`inject`.

    :param name: the resource name (defaults to ``default``)

    """
    return _Dependency(name)


@overload
def inject(
    func: Callable[P, Coroutine[Any, Any, T_Retval]],
) -> Callable[P, Coroutine[Any, Any, T_Retval]]:
    ...


@overload
def inject(func: Callable[P, T_Retval]) -> Callable[P, T_Retval]:
    ...


def inject(func: Callable[P, Any]) -> Callable[P, Any]:
    """
    Wrap the given coroutine function for use with dependency injection.

    Parameters with dependencies need to be annotated and have :func:`resource` as the
    default value. When the wrapped function is called, values for such parameters will
    be automatically filled in by calling :func:`require_resource` using the parameter's
    type annotation and the resource name passed to :func:`resource` (or ``"default"``)
    as the arguments.

    Any forward references among the type annotations are resolved on the first call to
    the wrapped function.

    """
    forward_refs_resolved = False
    local_names = sys._getframe(1).f_locals if "<locals>" in func.__qualname__ else {}

    def resolve_forward_refs() -> None:
        nonlocal forward_refs_resolved, local_names
        type_hints = get_type_hints(func, localns=local_names)
        for key, dependency in injected_resources.items():
            dependency.cls = type_hints[key]
            origin = get_origin(type_hints[key])
            if origin is Union or (
                sys.version_info >= (3, 10) and origin is types.UnionType
            ):
                args = [
                    arg for arg in get_args(dependency.cls) if arg is not type(None)
                ]
                if len(args) == 1:
                    dependency.optional = True
                    dependency.cls = args[0]
                else:
                    raise TypeError(
                        "Unions are only valid with dependency injection when there "
                        "are exactly two items and other item is None"
                    )

        del local_names
        forward_refs_resolved = True

    @wraps(func)
    def sync_wrapper(*args, **kwargs) -> Any:
        if not forward_refs_resolved:
            resolve_forward_refs()

        ctx = current_context()
        resources: dict[str, Any] = {}
        for argname, dependency in injected_resources.items():
            if dependency.optional:
                resources[argname] = ctx.get_resource(dependency.cls, dependency.name)
            else:
                resources[argname] = ctx.require_resource(
                    dependency.cls, dependency.name
                )

        return func(*args, **kwargs, **resources)

    @wraps(func)
    async def async_wrapper(*args, **kwargs) -> Any:
        if not forward_refs_resolved:
            resolve_forward_refs()

        ctx = current_context()
        resources: dict[str, Any] = {}
        for argname, dependency in injected_resources.items():
            if dependency.optional:
                resources[argname] = ctx.get_resource(dependency.cls, dependency.name)
            else:
                resources[argname] = ctx.require_resource(
                    dependency.cls, dependency.name
                )

        return await func(*args, **kwargs, **resources)

    sig = signature(func)
    injected_resources: dict[str, _Dependency] = {}
    for param in sig.parameters.values():
        if isinstance(param.default, _Dependency):
            if param.kind is Parameter.POSITIONAL_ONLY:
                raise TypeError(
                    f"Cannot inject dependency to positional-only parameter "
                    f"{param.name!r}"
                )

            if param.annotation is Parameter.empty:
                raise TypeError(
                    f"Dependency for parameter {param.name!r} of function "
                    f"{callable_name(func)!r} is missing the type annotation"
                )

            injected_resources[param.name] = param.default
        elif param.default is resource:
            raise TypeError(
                f"Default value for parameter {param.name!r} of function "
                f"{callable_name(func)} was the 'resource' function – did you forget "
                f"to add the parentheses at the end?"
            )

    if injected_resources:
        if iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    else:
        warnings.warn(
            f"{callable_name(func)} does not have any injectable resources declared"
        )
        return func
