from __future__ import annotations

import logging
import re
import sys
import types
import warnings
from collections.abc import AsyncGenerator, Coroutine, Sequence
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
    Optional,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from anyio import get_current_task

from ._event import Event, Signal, wait_event
from ._utils import callable_name, qualified_name

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup

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


class ResourceContainer:
    """
    Contains the resource value or its factory callable, plus some metadata.

    :ivar value_or_factory: the resource value or the factory callback
    :ivar types: type names the resource was registered with
    :vartype types: Tuple[type, ...]
    :ivar str name: name of the resource
    :ivar bool is_factory: ``True`` if ``value_or_factory`` if this is a resource
        factory
    """

    __slots__ = "value_or_factory", "types", "name", "is_factory"

    def __init__(
        self,
        value_or_factory: Any,
        types: tuple[type, ...],
        name: str,
        is_factory: bool,
    ) -> None:
        self.value_or_factory = value_or_factory
        self.types = types
        self.name = name
        self.is_factory = is_factory

    def generate_value(self, ctx: Context) -> Any:
        assert self.is_factory, "generate_value() only works for resource factories"
        value = self.value_or_factory(ctx)

        container = ResourceContainer(value, self.types, self.name, False)
        for type_ in self.types:
            ctx._resources[(type_, self.name)] = container

        return value

    def __repr__(self) -> str:
        typenames = ", ".join(qualified_name(cls) for cls in self.types)
        value_repr = (
            f"factory={callable_name(self.value_or_factory)}"
            if self.is_factory
            else f"value={self.value_or_factory!r}"
        )
        return (
            f"{self.__class__.__name__}({value_repr}, types=[{typenames}], "
            f"name={self.name!r})"
        )


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

    _reset_token: Token

    def __init__(self) -> None:
        self._parent = _current_context.get(None)
        self._state = ContextState.open
        self._resources: dict[tuple[type, str], ResourceContainer] = {}
        self._resource_factories: dict[tuple[type, str], ResourceContainer] = {}
        self._teardown_callbacks: list[tuple[Callable, bool]] = []

    @property
    def context_chain(self) -> list[Context]:
        """Return a list of contexts starting from this one, its parent and so on."""
        contexts = []
        ctx: Optional[Context] = self
        while ctx is not None:
            contexts.append(ctx)
            ctx = ctx.parent

        return contexts

    @property
    def parent(self) -> Optional[Context]:
        """Return the parent context, or ``None`` if there is no parent."""
        return self._parent

    @property
    def closed(self) -> bool:
        """
        Return ``True`` if the teardown process has at least been initiated, ``False``
        otherwise.

        """
        return self._state is not ContextState.open

    def _check_closed(self) -> None:
        if self._state is ContextState.closed:
            raise RuntimeError("this context has already been closed")

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
        self._check_closed()
        self._teardown_callbacks.append((callback, pass_exception))

    async def close(self, exception: BaseException | None = None) -> None:
        """
        Close this context and call any necessary resource teardown callbacks.

        If a teardown callback returns an awaitable, the return value is awaited on
        before calling any further teardown callbacks.

        All callbacks will be processed, even if some of them raise exceptions. If at
        least one callback raised an error, those exceptions are reraised in an
        exception group at the end.

        After this method has been called, resources can no longer be requested or
        published on this context.

        :param exception: the exception, if any, that caused this context to be closed

        """
        self._check_closed()
        if self._state is ContextState.closing:
            raise RuntimeError("this context is already closing")

        self._state = ContextState.closing

        try:
            exceptions = []
            while self._teardown_callbacks:
                callbacks, self._teardown_callbacks = self._teardown_callbacks, []
                for callback, pass_exception in reversed(callbacks):
                    try:
                        retval = callback(exception) if pass_exception else callback()
                        if isawaitable(retval):
                            await retval
                    except Exception as e:
                        exceptions.append(e)

            del self._teardown_callbacks
            if exceptions:
                raise BaseExceptionGroup(
                    "Exceptions were raised during context teardown", exceptions
                )
        finally:
            self._state = ContextState.closed

    async def __aenter__(self):
        self._check_closed()
        self._host_task = get_current_task()
        self._reset_token = _current_context.set(self)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> None:
        try:
            await self.close(exc_val)
        finally:
            try:
                _current_context.reset(self._reset_token)
            except ValueError:
                warnings.warn(
                    f"Potential context stack corruption detected. This context "
                    f"({hex(id(self))}) was entered in task {self._host_task} and "
                    f"exited in task {get_current_task()}. If this happened because "
                    f"you entered the context in an async generator, you should try to "
                    f"defer that to a regular async function."
                )

    async def add_resource(
        self,
        value,
        name: str = "default",
        types: type | Sequence[type] = (),
    ) -> None:
        """
        Add a resource to this context.

        This will cause a ``resource_added`` event to be dispatched.

        :param value: the actual resource value
        :param name: name of this resource (unique among all its registered types within
            a single context)
        :param types: type(s) to register the resource as (omit to use the type of
            ``value``)
        :raises asphalt.core.context.ResourceConflict: if the resource conflicts with an
            existing one in any way

        """
        self._check_closed()
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

        resource = ResourceContainer(value, types_, name, False)
        for type_ in resource.types:
            self._resources[(type_, name)] = resource

        # Notify listeners that a new resource has been made available
        await self.resource_added.dispatch(ResourceEvent(types_, name, False))

    async def add_resource_factory(
        self,
        factory_callback: factory_callback_type,
        types: type | Sequence[type] | None = None,
        name: str = "default",
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
        :param types: one or more types to register the generated resource as on the
            target context (can be omitted if the factory callable has a return type
            annotation)
        :param name: name of the resource that will be created in the target context
        :raises asphalt.core.context.ResourceConflict: if there is an existing resource
            factory for the given type/name combinations or the given context variable

        """
        import types as stdlib_types

        self._check_closed()
        if not resource_name_re.fullmatch(name):
            raise ValueError(
                '"name" must be a nonempty string consisting only of alphanumeric '
                "characters and underscores"
            )
        if iscoroutinefunction(factory_callback):
            raise TypeError('"factory_callback" must not be a coroutine function')

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
                resource_types = return_type_hint.__args__
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
        resource = ResourceContainer(factory_callback, resource_types, name, True)
        for type_ in resource_types:
            self._resource_factories[(type_, name)] = resource

        # Notify listeners that a new resource has been made available
        await self.resource_added.dispatch(ResourceEvent(resource_types, name, True))

    def get_resource(
        self, type: type[T_Resource], name: str = "default"
    ) -> Optional[T_Resource]:
        """
        Look up a resource in the chain of contexts.

        :param type: type of the requested resource
        :param name: name of the requested resource
        :return: the requested resource, or ``None`` if none was available

        """
        self._check_closed()
        key = (type, name)

        # First check if there's already a matching resource in this context
        resource = self._resources.get(key)
        if resource is not None:
            return resource.value_or_factory

        # Next, check if there's a resource factory available on the context chain
        resource = next(
            (
                ctx._resource_factories[key]
                for ctx in self.context_chain
                if key in ctx._resource_factories
            ),
            None,
        )
        if resource is not None:
            return resource.generate_value(self)

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
                container.name: container.generate_value(self)
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


@overload
def context_teardown(
    func: Callable[[T_Context], AsyncGenerator[None, Exception | None]]
) -> Callable[[T_Context], Coroutine[Any, Any, None]]:
    ...


@overload
def context_teardown(
    func: Callable[[T_Self, T_Context], AsyncGenerator[None, Exception | None]]
) -> Callable[[T_Self, T_Context], Coroutine[Any, Any, None]]:
    ...


def context_teardown(
    func: Callable[[T_Context], AsyncGenerator[None, Exception | None]]
    | Callable[[T_Self, T_Context], AsyncGenerator[None, Exception | None]]
) -> Callable[[T_Context], Coroutine[Any, Any, None]] | Callable[
    [T_Self, T_Context], Coroutine[Any, Any, None]
]:
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
        async def teardown_callback(exception: Optional[Exception]) -> None:
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


def get_resource(type: type[T_Resource], name: str = "default") -> T_Resource | None:
    """Shortcut for ``current_context().get_resource(...)``."""
    return current_context().get_resource(type, name)


def get_resources(type: type[T_Resource]) -> set[T_Resource]:
    """Shortcut for ``current_context().get_resources(...)``."""
    return current_context().get_resources(type)


def require_resource(type: type[T_Resource], name: str = "default") -> T_Resource:
    """Shortcut for ``current_context().require_resource(...)``."""
    return current_context().require_resource(type, name)


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
    func: Callable[P, Coroutine[Any, Any, T_Retval]]
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
                sys.version_info >= (3, 10) and origin is types.UnionType  # noqa: E721
            ):
                args = [
                    arg
                    for arg in get_args(dependency.cls)
                    if arg is not type(None)  # noqa: E721
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
                resource = ctx.get_resource(dependency.cls, dependency.name)
            else:
                resource = ctx.require_resource(dependency.cls, dependency.name)

            resources[argname] = resource

        return func(*args, **kwargs, **resources)

    @wraps(func)
    async def async_wrapper(*args, **kwargs) -> Any:
        if not forward_refs_resolved:
            resolve_forward_refs()

        ctx = current_context()
        resources: dict[str, Any] = {}
        for argname, dependency in injected_resources.items():
            if dependency.optional:
                resource = ctx.get_resource(dependency.cls, dependency.name)
            else:
                resource = ctx.require_resource(dependency.cls, dependency.name)

            resources[argname] = resource

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
