from __future__ import annotations

import logging
import re
import sys
import types
import warnings
from collections.abc import (
    AsyncGenerator,
    Coroutine,
    Mapping,
    Sequence,
)
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
    iscoroutine,
    iscoroutinefunction,
    signature,
)
from types import TracebackType
from typing import (
    Any,
    Callable,
    Generic,
    Literal,
    NoReturn,
    Optional,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from anyio import (
    create_task_group,
    get_current_task,
)
from anyio.abc import TaskGroup

from ._event import Event, Signal, wait_event
from ._exceptions import (
    AsyncResourceError,
    NoCurrentContext,
    ResourceConflict,
    ResourceNotFound,
)
from ._utils import callable_name, qualified_name

if sys.version_info >= (3, 10):
    from typing import ParamSpec, TypeAlias
else:
    from typing_extensions import ParamSpec, TypeAlias

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from exceptiongroup import BaseExceptionGroup
    from typing_extensions import Self

logger = logging.getLogger(__name__)
FactoryCallback: TypeAlias = Callable[[], Any]
TeardownCallback: TypeAlias = Union[
    Callable[[], Any], Callable[[Optional[BaseException]], Any]
]

resource_name_re = re.compile(r"\w+")
T_Resource = TypeVar("T_Resource")
T_Retval = TypeVar("T_Retval")
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
    teardown_callback: Callable[[], None | Coroutine[Any, Any, Any]] | None


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
    _reset_token: Token[Context]
    _exit_stack: AsyncExitStack

    def __init__(self) -> None:
        self._parent = _current_context.get(None)
        self._state = ContextState.inactive
        self._resources: dict[tuple[type, str], ResourceContainer] = {}
        self._resource_factories: dict[tuple[type, str], ResourceContainer] = {}
        self._teardown_callbacks: list[tuple[TeardownCallback, bool]] = []

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
                    retval = cast(Callable[[Optional[BaseException]], Any], callback)(
                        original_exception
                    )
                else:
                    retval = cast(Callable[[], Any], callback)()

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
        self, callback: TeardownCallback, pass_exception: bool = False
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

                # If this is the root task group, create and enter a task group
                if self._parent is None:
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

    def add_resource(
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
        :raises ResourceConflict: if the resource conflicts with an existing one in any
            way

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
        self.resource_added.dispatch(ResourceEvent(types_, name, False))

    def add_resource_factory(
        self,
        factory_callback: FactoryCallback,
        name: str = "default",
        *,
        types: Sequence[type] | None = None,
        description: str | None = None,
    ) -> None:
        """
        Add a resource factory to this context.

        This will cause a ``resource_added`` event to be dispatched.

        A resource factory is a callable that generates a "contextual" resource when it
        is requested by either :meth:`get_resource` or :meth:`get_resource_nowait`.

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
        :raises ResourceConflict: if there is an existing resource factory for the given
            type/name combinations or the given context variable

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
        self.resource_added.dispatch(ResourceEvent(resource_types, name, True))

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
        """
        Look up a resource in the chain of contexts.

        This method will not trigger async resource factories.

        :param type: type of the requested resource
        :param name: name of the requested resource
        :param optional: if ``True``, return ``None`` if the resource was not available
        :return: the requested resource, or ``None`` if none was available and
            ``optional`` was ``False``

        """
        self._ensure_state(ContextState.open, ContextState.closing)
        key = (type, name)

        # First check if there's already a matching resource in this context
        resource = self._resources.get(key)
        if resource is not None:
            return cast(T_Resource, resource.value_or_factory)

        # Next, check if there's a resource factory available on the context chain
        for ctx in self.context_chain:
            if key in ctx._resource_factories:
                # Call the factory callback to generate the resource
                factory = ctx._resource_factories[key]
                resource = factory.value_or_factory()

                # Raise AsyncResourceError if the factory returns a coroutine object
                if iscoroutine(resource):
                    resource.close()
                    raise AsyncResourceError()

                # Store the generated resource in the context
                container = ResourceContainer(
                    resource, factory.types, factory.name, factory.description, False
                )
                for type_ in factory.types:
                    self._resources[(type_, factory.name)] = container

                # Dispatch the resource_added event to notify any listeners
                self.resource_added.dispatch(ResourceEvent(factory.types, name, False))

                return cast(T_Resource, resource)

        # Finally, check parents for a matching resource
        for ctx in self.context_chain:
            if key in ctx._resources:
                return cast(T_Resource, ctx._resources[key].value_or_factory)

        if optional:
            return None

        raise ResourceNotFound(type, name)

    @overload
    async def get_resource(
        self,
        type: type[T_Resource],
        name: str = ...,
        *,
        wait: bool = False,
        optional: Literal[True],
    ) -> T_Resource | None: ...

    @overload
    async def get_resource(
        self,
        type: type[T_Resource],
        name: str = ...,
        *,
        wait: bool = ...,
        optional: Literal[False],
    ) -> T_Resource: ...

    @overload
    async def get_resource(
        self, type: type[T_Resource], name: str = ..., *, wait: bool = False
    ) -> T_Resource: ...

    async def get_resource(
        self,
        type: type[T_Resource],
        name: str = "default",
        *,
        wait: bool = False,
        optional: Literal[False, True] = False,
    ) -> T_Resource | None:
        """
        Look up a resource in the chain of contexts.

        :param type: type of the requested resource
        :param name: name of the requested resource
        :param wait: if ``True``, wait for the resource to become available if it's not
            already available in the context chain
        :param optional: if ``True``, return ``None`` if the resource was not available
        :raises ValueError: if both ``optional=True`` and ``wait=True`` were specified,
            as it doesn't make sense
        :return: the requested resource, or ``None`` if none was available and
            ``optional`` was ``False``

        """
        self._ensure_state(ContextState.open, ContextState.closing)

        if wait and optional:
            raise ValueError("combining wait=True and optional=True doesn't make sense")

        # First check if there's already a matching resource in this context
        key = (type, name)
        if (resource := self._resources.get(key)) is not None:
            return cast(T_Resource, resource.value_or_factory)

        # Next, check if there's a resource factory available on the context chain
        for ctx in self.context_chain:
            if key in ctx._resource_factories:
                factory = ctx._resource_factories[key]
                resource = factory.value_or_factory()
                if isawaitable(resource):
                    resource = await resource

                container = ResourceContainer(
                    resource, factory.types, factory.name, factory.description, False
                )
                for type_ in factory.types:
                    self._resources[(type_, factory.name)] = container

                # Dispatch the resource_added event to notify any listeners
                self.resource_added.dispatch(ResourceEvent(factory.types, name, False))

                return cast(T_Resource, resource)

        # Finally, check parents for a matching resource
        for ctx in self.context_chain:
            if key in ctx._resources:
                return cast(T_Resource, ctx._resources[key].value_or_factory)

        if wait:
            # Wait until a matching resource or resource factory is available
            signals = [ctx.resource_added for ctx in self.context_chain]
            await wait_event(
                signals,
                lambda event: event.resource_name == name
                and type in event.resource_types,
            )
            return await self.get_resource(type, name)
        elif optional:
            return None

        raise ResourceNotFound(type, name)

    def get_resources(self, type: type[T_Resource]) -> Mapping[str, T_Resource]:
        """
        Retrieve all the resources of the given type in this context and its parents.

        This method does not trigger resource factories; it only retrieves resources
        already in the context chain.

        :param type: type of the resources to get
        :return: a mapping of resource name to the resource

        """
        # Collect all the matching resources from this context
        resources: dict[str, T_Resource] = {
            container.name: container.value_or_factory
            for container in self._resources.values()
            if not container.is_factory and type in container.types
        }

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

        return resources


def context_teardown(
    func: Callable[P, AsyncGenerator[None, BaseException | None]],
) -> Callable[P, Coroutine[Any, Any, None]]:
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
            async def start(self):
                service = SomeService()
                add_resource(service)
                exception = yield
                service.stop()

    :param func: an async generator function
    :return: an async function

    """

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        async def teardown_callback(exception: BaseException | None) -> None:
            try:
                await generator.asend(exception)
            except StopAsyncIteration:
                pass
            finally:
                await generator.aclose()

        try:
            ctx = current_context()
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


def add_resource(
    value: T_Resource,
    name: str = "default",
    types: type | Sequence[type] = (),
    *,
    description: str | None = None,
    teardown_callback: Callable[[], Any] | None = None,
) -> None:
    """
    Shortcut for ``current_context().add_resource(...)``.

    .. seealso:: :meth:`Context.add_resource`
    """
    current_context().add_resource(
        value, name, types, description=description, teardown_callback=teardown_callback
    )


def add_resource_factory(
    factory_callback: FactoryCallback,
    name: str = "default",
    *,
    types: Sequence[type] | None = None,
    description: str | None = None,
) -> None:
    """
    Shortcut for ``current_context().add_resource_factory(...)``.

    .. seealso:: :meth:`Context.add_resource_factory`

    """
    current_context().add_resource_factory(
        factory_callback, name, types=types, description=description
    )


def add_teardown_callback(
    callback: TeardownCallback, pass_exception: bool = False
) -> None:
    """
    Shortcut for ``current_context().add_teardown_callback(...)``.

    .. seealso:: :meth:`Context.add_teardown_callback`
    """
    current_context().add_teardown_callback(callback, pass_exception=pass_exception)


def get_resources(type: type[T_Resource]) -> Mapping[str, T_Resource]:
    """
    Shortcut for ``current_context().get_resources(...)``.

    .. seealso:: :meth:`Context.get_resources`

    """
    return current_context().get_resources(type)


@overload
async def get_resource(
    type: type[T_Resource],
    name: str = ...,
    *,
    wait: bool = False,
    optional: Literal[True],
) -> T_Resource | None: ...


@overload
async def get_resource(
    type: type[T_Resource],
    name: str = ...,
    *,
    wait: bool = ...,
    optional: Literal[False],
) -> T_Resource: ...


@overload
async def get_resource(
    type: type[T_Resource], name: str = ..., *, wait: bool = False
) -> T_Resource: ...


async def get_resource(
    type: type[T_Resource],
    name: str = "default",
    *,
    wait: bool = False,
    optional: Literal[False, True] = False,
) -> T_Resource | None:
    """
    Shortcut for ``current_context().get_resource(...)``.

    .. seealso:: :meth:`Context.get_resource`

    """
    return await current_context().get_resource(
        type, name, wait=wait, optional=optional
    )


@overload
def get_resource_nowait(
    type: type[T_Resource], name: str = "default", *, optional: Literal[True]
) -> T_Resource | None: ...


@overload
def get_resource_nowait(
    type: type[T_Resource], name: str = "default", *, optional: Literal[False]
) -> T_Resource: ...


@overload
def get_resource_nowait(
    type: type[T_Resource], name: str = "default"
) -> T_Resource: ...


def get_resource_nowait(
    type: type[T_Resource],
    name: str = "default",
    *,
    optional: Literal[False, True] = False,
) -> T_Resource | None:
    """
    Shortcut for ``current_context().get_resource_nowait(...)``.

    .. seealso:: :meth:`Context.get_resource`

    """
    return current_context().get_resource_nowait(type, name, optional=optional)


@dataclass
class _Dependency:
    name: str = "default"
    cls: type = field(init=False, repr=False)
    optional: bool = field(init=False, default=False)

    def __getattr__(self, item: str) -> NoReturn:
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
) -> Callable[P, Coroutine[Any, Any, T_Retval]]: ...


@overload
def inject(func: Callable[P, T_Retval]) -> Callable[P, T_Retval]: ...


def inject(func: Callable[P, Any]) -> Callable[P, Any]:
    """
    Wrap the given coroutine function for use with dependency injection.

    Parameters with dependencies need to be annotated and have :func:`resource` as the
    default value. When the wrapped function is called, values for such parameters will
    be automatically filled in by calling :func:`get_resource` using the parameter's
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

    def resolve_resources() -> dict[str, Any]:
        if not forward_refs_resolved:
            resolve_forward_refs()

        ctx = current_context()
        resources: dict[str, Any] = {}
        for argname, dependency in injected_resources.items():
            if dependency.optional:
                resources[argname] = ctx.get_resource_nowait(
                    dependency.cls, dependency.name, optional=True
                )
            else:
                resources[argname] = ctx.get_resource_nowait(
                    dependency.cls, dependency.name
                )

        return resources

    @wraps(func)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
        __tracebackhide__ = True
        return func(*args, **kwargs, **resolve_resources())

    async def resolve_resources_async() -> dict[str, Any]:
        if not forward_refs_resolved:
            resolve_forward_refs()

        ctx = current_context()
        resources: dict[str, Any] = {}
        for argname, dependency in injected_resources.items():
            if dependency.optional:
                resources[argname] = await ctx.get_resource(
                    dependency.cls, dependency.name, optional=True
                )
            else:
                resources[argname] = await ctx.get_resource(
                    dependency.cls, dependency.name
                )

        return resources

    @wraps(func)
    async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
        __tracebackhide__ = True
        return await func(*args, **kwargs, **await resolve_resources_async())

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
