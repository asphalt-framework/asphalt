import logging
import re
from asyncio import get_event_loop, iscoroutinefunction, AbstractEventLoop
from concurrent.futures import Executor
from functools import wraps
from inspect import isawaitable, getattr_static
from traceback import format_exception
from typing import (
    Optional, Callable, Any, Sequence, Dict, Tuple, Type, List, Union, Awaitable, TypeVar, Set)

import asyncio_extras
from async_generator import async_generator, isasyncgenfunction
from typeguard import check_argument_types

from asphalt.core.event import Signal, Event, wait_event
from asphalt.core.utils import qualified_name, callable_name

__all__ = ('ResourceEvent', 'ResourceConflict', 'ResourceNotFound', 'TeardownError', 'Context',
           'executor', 'context_teardown')

logger = logging.getLogger(__name__)
factory_callback_type = Callable[['Context'], Any]
resource_name_re = re.compile(r'\w+')
T_Resource = TypeVar('T_Resource', covariant=True)


class ResourceContainer:
    """
    Contains the resource value or its factory callable, plus some metadata.

    :ivar value_or_factory: the resource value or the factory callback
    :ivar types: type names the resource was registered with
    :vartype types: Tuple[type, ...]
    :ivar str name: name of the resource
    :ivar str context_attr: the context attribute of the resource
    :ivar bool is_factory: ``True`` if ``value_or_factory`` if this is a resource factory
    """

    __slots__ = 'value_or_factory', 'types', 'name', 'context_attr', 'is_factory'

    def __init__(self, value_or_factory, types: Tuple[type, ...], name: str,
                 context_attr: Optional[str], is_factory: bool) -> None:
        self.value_or_factory = value_or_factory
        self.types = types
        self.name = name
        self.context_attr = context_attr
        self.is_factory = is_factory

    def generate_value(self, ctx: 'Context'):
        assert self.is_factory, 'generate_value() only works for resource factories'
        value = self.value_or_factory(ctx)

        container = ResourceContainer(value, self.types, self.name, self.context_attr, False)
        for type_ in self.types:
            ctx._resources[(type_, self.name)] = container

        if self.context_attr:
            setattr(ctx, self.context_attr, value)

        return value

    def __repr__(self):
        typenames = ', '.join(qualified_name(cls) for cls in self.types)
        value_repr = ('factory=%s' % callable_name(self.value_or_factory) if self.is_factory
                      else 'value=%r' % self.value_or_factory)
        return ('{self.__class__.__name__}({value_repr}, types=[{typenames}], name={self.name!r}, '
                'context_attr={self.context_attr!r})'.format(
                    self=self, value_repr=value_repr, typenames=typenames))


class ResourceEvent(Event):
    """
    Dispatched when a resource or resource factory has been added to a context.

    :ivar resource_types: types the resource was registered under
    :vartype resource_types: Tuple[type, ...]
    :ivar str name: name of the resource
    :ivar bool is_factory: ``True`` if a resource factory was added, ``False`` if a regular
        resource was added
    """

    __slots__ = 'resource_types', 'resource_name', 'is_factory'

    def __init__(self, source: 'Context', topic: str, types: Tuple[type, ...], name: str,
                 is_factory: bool) -> None:
        super().__init__(source, topic)
        self.resource_types = types
        self.resource_name = name
        self.is_factory = is_factory


class ResourceConflict(Exception):
    """
    Raised when a new resource that is being published conflicts with an existing resource or
    context variable.
    """


class ResourceNotFound(LookupError):
    """Raised when a resource request cannot be fulfilled within the allotted time."""

    def __init__(self, type: type, name: str) -> None:
        super().__init__(type, name)
        self.type = type
        self.name = name

    def __str__(self):
        return 'no matching resource was found for type={typename} name={self.name!r}'.\
            format(self=self, typename=qualified_name(self.type))


class TeardownError(Exception):
    """
    Raised after context teardown when one or more teardown callbacks raised an exception.

    :ivar exceptions: exceptions raised during context teardown, in the order in which they were
        raised
    :vartype exceptions: List[Exception]
    """

    def __init__(self, exceptions: List[Exception]) -> None:
        super().__init__(exceptions)
        self.exceptions = exceptions

    def __str__(self):
        separator = '----------------------------\n'
        tracebacks = separator.join('\n'.join(format_exception(type(exc), exc, exc.__traceback__))
                                    for exc in self.exceptions)
        return '{} exceptions(s) were raised by teardown callbacks:\n{}{}'.\
            format(len(self.exceptions), separator, tracebacks)


class Context:
    """
    Contexts give request handlers and callbacks access to resources.

    Contexts are stacked in a way that accessing an attribute that is not present in the current
    context causes the attribute to be looked up in the parent instance and so on, until the
    attribute is found (or :class:`AttributeError` is raised).

    :param parent: the parent context, if any

    :ivar Context parent: the parent context, if any
    :var Signal resource_added: a signal (:class:`ResourceEvent`) dispatched when a resource
        has been published in this context
    """

    resource_added = Signal(ResourceEvent)

    def __init__(self, parent: 'Context' = None) -> None:
        assert check_argument_types()
        self._parent = parent
        self._loop = getattr(parent, 'loop', None) or get_event_loop()
        self._closed = False
        self._resources = {}  # type: Dict[Tuple[type, str], ResourceContainer]
        self._resource_factories = {}  # type: Dict[Tuple[type, str], ResourceContainer]
        self._resource_factories_by_context_attr = {}  # type: Dict[str, ResourceContainer]
        self._teardown_callbacks = []  # type: List[Tuple[Callable, bool]]

    def __getattr__(self, name):
        # First look for a resource factory in the whole context chain
        for ctx in self.context_chain:
            factory = ctx._resource_factories_by_context_attr.get(name)
            if factory:
                return factory.generate_value(self)

        # When that fails, look directly for an attribute in the parents
        for ctx in self.context_chain[1:]:
            value = getattr_static(ctx, name, None)
            if value is not None:
                return getattr(ctx, name)

        raise AttributeError('no such context variable: {}'.format(name))

    @property
    def context_chain(self) -> List['Context']:
        """Return a list of contexts starting from this one, its parent and so on."""
        contexts = []
        ctx = self
        while ctx is not None:
            contexts.append(ctx)
            ctx = ctx.parent

        return contexts

    @property
    def loop(self) -> AbstractEventLoop:
        """Return the event loop associated with this context."""
        return self._loop

    @property
    def parent(self) -> Optional['Context']:
        """Return the parent context, or ``None`` if there is no parent."""
        return self._parent

    @property
    def closed(self) -> bool:
        """Return ``True`` if the context has been closed, ``False`` otherwise."""
        return self._closed

    def _check_closed(self):
        if self._closed:
            raise RuntimeError('this context has already been closed')

    def add_teardown_callback(self, callback: Callable, pass_exception: bool = False) -> None:
        """
        Add a callback to be called when this context closes.

        This is intended for cleanup of resources, and the list of callbacks is processed in the
        reverse order in which they were added, so the last added callback will be called first.

        The callback may return an awaitable. If it does, the awaitable is awaited on before
        calling any further callbacks.

        :param callback: a callable that is called with either no arguments or with the exception
            that ended this context, based on the value of ``pass_exception``
        :param pass_exception: ``True`` to pass the callback the exception that ended this context
            (or ``None`` if the context ended cleanly)

        """
        assert check_argument_types()
        self._check_closed()
        self._teardown_callbacks.append((callback, pass_exception))

    async def close(self, exception: BaseException = None) -> None:
        """
        Close this context and call any necessary resource teardown callbacks.

        If a teardown callback returns an awaitable, the return value is awaited on before calling
        any further teardown callbacks.

        All callbacks will be processed, even if some of them raise exceptions. If at least one
        callback raised an error, this method will raise a :exc:`~.TeardownError` at the end.

        After this method has been called, resources can no longer be requested or published on
        this context.

        :param exception: the exception, if any, that caused this context to be closed
        :raises .TeardownError: if one or more teardown callbacks raise an exception

        """
        self._check_closed()
        self._closed = True

        exceptions = []
        for callback, pass_exception in reversed(self._teardown_callbacks):
            try:
                retval = callback(exception) if pass_exception else callback()
                if isawaitable(retval):
                    await retval
            except Exception as e:
                exceptions.append(e)

        del self._teardown_callbacks
        if exceptions:
            raise TeardownError(exceptions)

    def __enter__(self):
        self._check_closed()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.loop.run_until_complete(self.close(exc_val))

    async def __aenter__(self):
        self._check_closed()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close(exc_val)

    def add_resource(self, value, name: str = 'default', context_attr: str = None,
                     types: Union[type, Sequence[type]] = ()) -> None:
        """
        Add a resource to this context.

        This will cause a ``resource_added`` event to be dispatched.

        :param value: the actual resource value
        :param name: name of this resource (unique among all its registered types within a single
            context)
        :param context_attr: name of the context attribute this resource will be accessible as
        :param types: type(s) to register the resource as (omit to use the type of ``value``)
        :raises asphalt.core.context.ResourceConflict: if the resource conflicts with an existing
            one in any way

        """
        assert check_argument_types()
        self._check_closed()
        if isinstance(types, type):
            types = (types,)
        elif not types:
            types = (type(value),)

        if value is None:
            raise ValueError('"value" must not be None')
        if not resource_name_re.fullmatch(name):
            raise ValueError('"name" must be a nonempty string consisting only of alphanumeric '
                             'characters and underscores')
        if context_attr and getattr_static(self, context_attr, None) is not None:
            raise ResourceConflict('this context already has an attribute {!r}'.format(
                context_attr))
        for resource_type in types:
            if (resource_type, name) in self._resources:
                raise ResourceConflict(
                    'this context already contains a resource of type {} using the name {!r}'.
                    format(qualified_name(resource_type), name))

        resource = ResourceContainer(value, tuple(types), name, context_attr, False)
        for type_ in resource.types:
            self._resources[(type_, name)] = resource

        if context_attr:
            setattr(self, context_attr, value)

        # Notify listeners that a new resource has been made available
        self.resource_added.dispatch(types, name, False)

    def add_resource_factory(self, factory_callback: factory_callback_type,
                             types: Union[type, Sequence[Type]], name: str = 'default',
                             context_attr: str = None) -> None:
        """
        Add a resource factory to this context.

        This will cause a ``resource_added`` event to be dispatched.

        A resource factory is a callable that generates a "contextual" resource when it is
        requested by either using any of the methods :meth:`get_resource`, :meth:`require_resource`
        or :meth:`request_resource` or its context attribute is accessed.

        When a new resource is created in this manner, it is always bound to the context through
        it was requested, regardless of where in the chain the factory itself was added to.

        :param factory_callback: a (non-coroutine) callable that takes a context instance as
            argument and returns the created resource object
        :param types: one or more types to register the generated resource as on the target context
        :param name: name of the resource that will be created in the target context
        :param context_attr: name of the context attribute the created resource will be accessible
            as
        :raises asphalt.core.context.ResourceConflict: if there is an existing resource factory for
            the given type/name combinations or the given context variable

        """
        assert check_argument_types()
        self._check_closed()
        if not resource_name_re.fullmatch(name):
            raise ValueError('"name" must be a nonempty string consisting only of alphanumeric '
                             'characters and underscores')
        if iscoroutinefunction(factory_callback):
            raise TypeError('"factory_callback" must not be a coroutine function')
        if not types:
            raise ValueError('"types" must not be empty')

        if isinstance(types, type):
            resource_types = (types,)  # type: Tuple[type, ...]
        else:
            resource_types = tuple(types)

        # Check for a conflicting context attribute
        if context_attr in self._resource_factories_by_context_attr:
            raise ResourceConflict(
                'this context already contains a resource factory for the context attribute {!r}'.
                format(context_attr))

        # Check for conflicts with existing resource factories
        for type_ in resource_types:
            if (type_, name) in self._resource_factories:
                raise ResourceConflict('this context already contains a resource factory for the '
                                       'type {}'.format(qualified_name(type_)))

        # Add the resource factory to the appropriate lookup tables
        resource = ResourceContainer(factory_callback, resource_types, name, context_attr, True)
        for type_ in resource_types:
            self._resource_factories[(type_, name)] = resource

        if context_attr:
            self._resource_factories_by_context_attr[context_attr] = resource

        # Notify listeners that a new resource has been made available
        self.resource_added.dispatch(resource_types, name, True)

    def get_resource(self, type: Type[T_Resource], name: str = 'default') -> Optional[T_Resource]:
        """
        Look up a resource in the chain of contexts.

        :param type: type of the requested resource
        :param name: name of the requested resource
        :return: the requested resource, or ``None`` if none was available

        """
        assert check_argument_types()
        self._check_closed()
        key = (type, name)

        # First check if there's already a matching resource in this context
        resource = self._resources.get(key)
        if resource is not None:
            return resource.value_or_factory

        # Next, check if there's a resource factory available on the context chain
        resource = next((ctx._resource_factories[key] for ctx in self.context_chain
                         if key in ctx._resource_factories), None)
        if resource is not None:
            return resource.generate_value(self)

        # Finally, check parents for a matching resource
        return next((ctx._resources[key].value_or_factory for ctx in self.context_chain
                     if key in ctx._resources), None)

    def get_resources(self, type: Type[T_Resource]) -> Set[T_Resource]:
        """
        Retrieve all the resources of the given type in this context and its parents.

        Any matching resource factories are also triggered if necessary.

        :param type: type of the resources to get
        :return: a set of all found resources of the given type

        """
        assert check_argument_types()

        # Collect all the matching resources from this context
        resources = {container.name: container.value_or_factory
                     for container in self._resources.values()
                     if not container.is_factory and type in container.types
                     }  # type: Dict[str, T_Resource]

        # Next, find all matching resource factories in the context chain and generate resources
        resources.update({container.name: container.generate_value(self)
                          for ctx in self.context_chain
                          for container in ctx._resources.values()
                          if container.is_factory and type in container.types and
                          container.name not in resources})

        # Finally, add the resource values from the parent contexts
        resources.update({container.name: container.value_or_factory
                          for ctx in self.context_chain[1:]
                          for container in ctx._resources.values()
                          if not container.is_factory and type in container.types and
                          container.name not in resources})

        return set(resources.values())

    def require_resource(self, type: Type[T_Resource], name: str = 'default') -> T_Resource:
        """
        Look up a resource in the chain of contexts and raise an exception if it is not found.

        This is like :meth:`get_resource` except that instead of returning ``None`` when a resource
        is not found, it will raise :exc:`~asphalt.core.context.ResourceNotFound`.

        :param type: type of the requested resource
        :param name: name of the requested resource
        :return: the requested resource
        :raises asphalt.core.context.ResourceNotFound: if a resource of the given type and name was
            not found

        """
        resource = self.get_resource(type, name)
        if resource is None:
            raise ResourceNotFound(type, name)

        return resource

    async def request_resource(self, type: Type[T_Resource], name: str = 'default') -> T_Resource:
        """
        Look up a resource in the chain of contexts.

        This is like :meth:`get_resource` except that if the resource is not already available, it
        will wait for one to become available.

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
            signals, lambda event: event.resource_name == name and type in event.resource_types)
        return self.get_resource(type, name)

    def call_async(self, func: Callable, *args, **kwargs):
        """
        Call the given callable in the event loop thread.

        This method lets you call asynchronous code from a worker thread.
        Do not use it from within the event loop thread.

        If the callable returns an awaitable, it is resolved before returning to the caller.

        :param func: a regular function or a coroutine function
        :param args: positional arguments to call the callable with
        :param kwargs: keyword arguments to call the callable with
        :return: the return value of the call

        """
        return asyncio_extras.call_async(self.loop, func, *args, **kwargs)

    def call_in_executor(self, func: Callable, *args, executor: Union[Executor, str] = None,
                         **kwargs) -> Awaitable:
        """
        Call the given callable in an executor.

        :param func: the callable to call
        :param args: positional arguments to call the callable with
        :param executor: either an :class:`~concurrent.futures.Executor` instance, the resource
            name of one or ``None`` to use the event loop's default executor
        :param kwargs: keyword arguments to call the callable with
        :return: an awaitable that resolves to the return value of the call

        """
        assert check_argument_types()
        if isinstance(executor, str):
            executor = self.require_resource(Executor, executor)

        return asyncio_extras.call_in_executor(func, *args, executor=executor, **kwargs)

    def threadpool(self, executor: Union[Executor, str] = None):
        """
        Return an asynchronous context manager that runs the block in a (thread pool) executor.

        :param executor: either an :class:`~concurrent.futures.Executor` instance, the resource
            name of one or ``None`` to use the event loop's default executor
        :return: an asynchronous context manager

        """
        assert check_argument_types()
        if isinstance(executor, str):
            executor = self.require_resource(Executor, executor)

        return asyncio_extras.threadpool(executor)


def executor(arg: Union[Executor, str, Callable] = None):
    """
    Decorate a function so that it runs in an :class:`~concurrent.futures.Executor`.

    If a resource name is given, the first argument must be a :class:`~.Context`.

    Usage::

        @executor
        def should_run_in_executor():
            ...

    With a resource name::

        @executor('resourcename')
        def should_run_in_executor(ctx):
            ...

    :param arg: a callable to decorate, an :class:`~concurrent.futures.Executor` instance, the
        resource name of one or ``None`` to use the event loop's default executor
    :return: the wrapped function

    """
    def outer_wrapper(func: Callable):
        @wraps(func)
        def inner_wrapper(*args, **kwargs):
            try:
                ctx = next(arg for arg in args[:2] if isinstance(arg, Context))
            except StopIteration:
                raise RuntimeError('the first positional argument to {}() has to be a Context '
                                   'instance'.format(callable_name(func))) from None

            executor = ctx.require_resource(Executor, resource_name)
            return asyncio_extras.call_in_executor(func, *args, executor=executor, **kwargs)

        return inner_wrapper

    if isinstance(arg, str):
        resource_name = arg
        return outer_wrapper

    return asyncio_extras.threadpool(arg)


def context_teardown(func: Callable):
    """
    Wrap an async generator function to execute the rest of the function at context teardown.

    This function returns an async function, which, when called, starts the wrapped async
    generator. The wrapped async function is run until the first ``yield`` statement
    (``await async_generator.yield_()`` on Python 3.5). When the context is being torn down, the
    exception that ended the context, if any, is sent to the generator.

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
        async def teardown_callback(exception: Optional[Exception]):
            try:
                await generator.asend(exception)
            except StopAsyncIteration:
                pass
            finally:
                await generator.aclose()

        try:
            ctx = next(arg for arg in args[:2] if isinstance(arg, Context))
        except StopIteration:
            raise RuntimeError('the first positional argument to {}() has to be a Context '
                               'instance'.format(callable_name(func))) from None

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

    if iscoroutinefunction(func):
        func = async_generator(func)
    elif not isasyncgenfunction(func):
        raise TypeError('{} must be an async generator function'.format(callable_name(func)))

    return wrapper
