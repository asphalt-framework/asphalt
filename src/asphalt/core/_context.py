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
    Literal,
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

from ._event import Event, Signal
from ._utils import callable_name, qualified_name

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup

logger = logging.getLogger(__name__)
factory_callback_type = Callable[[], Any]
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
        *,
        description: str | None = None,
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

        container = ResourceContainer(value, types_, name, description, False)
        for type_ in types_:
            self._resources[(type_, name)] = container

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

        self._check_closed()
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
        resource = ResourceContainer(
            factory_callback, resource_types, name, description, True
        )
        for type_ in resource_types:
            self._resource_factories[(type_, name)] = resource

        # Notify listeners that a new resource has been made available
        await self.resource_added.dispatch(ResourceEvent(resource_types, name, True))

    def _get_resource(
        self, type: type[T_Resource], name: str
    ) -> ResourceContainer | None:
        self._check_closed()
        key = (type, name)

        # First check if there's already a matching resource in this context
        if key in self._resources:
            return self._resources[key]

        # Next, check if there's a resource factory available on the context chain
        container = next(
            (
                ctx._resource_factories[key]
                for ctx in self.context_chain
                if key in ctx._resource_factories
            ),
            None,
        )
        if container is not None:
            return container

        # Finally, check parents for a matching resource
        return next(
            (
                ctx._resources[key]
                for ctx in self.context_chain
                if key in ctx._resources
            ),
            None,
        )

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

    @overload
    async def get_resource(
        self, type: type[T_Resource], name: str = ..., *, optional: Literal[True]
    ) -> T_Resource | None:
        ...

    @overload
    async def get_resource(
        self, type: type[T_Resource], name: str = ..., *, optional: Literal[False] = ...
    ) -> T_Resource:
        ...

    async def get_resource(
        self, type: Any, name: str = "default", *, optional: bool = False
    ) -> Any:
        """
        Look up a resource in the chain of contexts.

        The resource lookup order is as follows:

        #. static resources in current context
        #. resource factories in the context chain
        #. static resources in parent contexts

        :param type: type of the requested resource
        :param name: name of the requested resource
        :param optional: ``True`` to return ``None`` if no matching resource was found
        :return: the requested resource
        :raises ~.ResourceNotFound: if the resource was required but none was found

        """
        resource = self._get_resource(type, name)
        if resource is None:
            if not optional:
                raise ResourceNotFound(type, name)
            else:
                return None

        if resource.is_factory:
            retval = resource.value_or_factory(self)
            if isawaitable(retval):
                value = await retval
            else:
                value = retval

            await self.add_resource(value, name, resource.types)
            return retval
        else:
            return resource.value_or_factory

    @overload
    def get_resource_nowait(
        self,
        type: type[T_Resource] | None,
        name: str = ...,
        *,
        optional: Literal[True],
    ) -> T_Resource | None:
        ...

    @overload
    def get_resource_nowait(
        self, type: type[T_Resource], name: str = ..., *, optional: Literal[False] = ...
    ) -> T_Resource:
        ...

    def get_resource_nowait(
        self, type: Any, name: str = "default", *, optional: bool = False
    ) -> Any:
        """
        Synchronous version of :meth:`get_resource`.

        This method can be used to retrieve resources in a synchronous callback.
        If an asynchronous resource factory is required to generate the requested
        resource, :exc:`~.AwaitableResourceError` is raised.

        :param type: type of the requested resource
        :param name: name of the requested resource
        :param optional: ``True`` to return ``None`` if no matching resource was found
        :return: the requested resource, or ``None`` if the resource was optional and
            none was available
        :raises ~.ResourceNotFound: if the resource was required but none was found
        :raises ~.AwaitableResourceError: if using an asynchronous resource factory was
            required to fulfill the request

        """
        resource = self._get_resource(type, name)
        if resource is None:
            if optional:
                return None
            else:
                raise ResourceNotFound(type, name)

        if resource.is_factory:
            if iscoroutinefunction(resource):
                raise AwaitableResourceError

            value = resource.value_or_factory(self)
            if resource.description:
                description = f"Resource generated from {resource.description}"
            else:
                description = None

            container = ResourceContainer(
                value, resource.types, name, description, False
            )
            for type_ in resource.types:
                self._resources[(type_, name)] = container

            return value

        return resource.value_or_factory


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


def get_static_resources(type: type[T_Resource]) -> set[T_Resource]:
    """Shortcut for ``current_context().get_static_resources(...)``."""
    return current_context().get_static_resources(type)


@overload
def get_resource_nowait(
    type: type[T_Resource], name: str = "default", *, optional: Literal[True]
) -> T_Resource | None:
    ...


@overload
def get_resource_nowait(
    type: type[T_Resource], name: str = "default", *, optional: Literal[False] = False
) -> T_Resource:
    ...


def get_resource_nowait(
    type: type[T_Resource], name: str = "default", *, optional: bool = False
) -> T_Resource | None:
    """Shortcut for ``current_context().get_resource(...)``."""
    if optional:
        return current_context().get_resource_nowait(type, name, optional=True)
    else:
        return current_context().get_resource_nowait(type, name, optional=False)


@overload
async def get_resource(
    type: type[T_Resource], name: str = ..., *, optional: Literal[True]
) -> T_Resource | None:
    ...


@overload
async def get_resource(
    type: type[T_Resource], name: str = ..., *, optional: Literal[False] = ...
) -> T_Resource:
    ...


async def get_resource(
    type: type[T_Resource], name: str = "default", *, optional: bool = False
) -> T_Resource | None:
    """Shortcut for ``current_context().require_resource(...)``."""
    if optional:
        return await current_context().get_resource(type, name, optional=True)
    else:
        return await current_context().get_resource(type, name, optional=False)


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
                resources[argname] = ctx.get_resource_nowait(
                    dependency.cls, dependency.name, optional=True
                )
            else:
                resources[argname] = ctx.get_resource_nowait(
                    dependency.cls, dependency.name, optional=False
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
                resources[argname] = await ctx.get_resource(
                    dependency.cls, dependency.name, optional=True
                )
            else:
                resources[argname] = await ctx.get_resource(
                    dependency.cls, dependency.name, optional=False
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
