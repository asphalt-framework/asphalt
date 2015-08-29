from asyncio import coroutine, iscoroutinefunction, iscoroutine, get_event_loop, async
from concurrent.futures import Future
from importlib import import_module
from threading import current_thread, main_thread
from typing import Callable, Any, Container
from functools import wraps, partial

__all__ = ('resolve_reference', 'qualified_name', 'synchronous', 'asynchronous',
           'wrap_blocking_api', 'wrap_async_api')


def resolve_reference(ref):
    """
    Returns the object pointed to by ``ref``. If ``ref`` is not a string, it is returned as is.
    References must be in the form <modulename>:<varname> where <modulename> is the fully
    qualified module name and varname is the path to the variable inside that module.

    For example, "concurrent.futures:Future" would give you the Future class.

    :raises ValueError: if there was no : in the reference string
    :raises LookupError: if the reference could not be resolved
    """

    if not isinstance(ref, str):
        return ref
    if ':' not in ref:
        raise ValueError('invalid reference (no ":" contained in the string)')

    modulename, rest = ref.split(':', 1)
    try:
        obj = import_module(modulename)
    except ImportError:
        raise LookupError('error resolving reference {}: could not import module'.format(ref))

    try:
        for name in rest.split('.'):
            obj = getattr(obj, name)
        return obj
    except AttributeError:
        raise LookupError('error resolving reference {}: error looking up object'.format(ref))


def qualified_name(obj) -> str:
    """Returns the qualified name (e.g. package.module.Type) for the given object."""

    try:
        module = obj.__module__
        qualname = obj.__qualname__
    except AttributeError:
        type_ = type(obj)
        module = type_.__module__
        qualname = type_.__qualname__

    return qualname if module == 'builtins' else '{}.{}'.format(module, qualname)


def synchronous(func: Callable[..., Any]):
    """
    Returns a wrapper that guarantees that the target callable will be run in a thread other than
    the event loop thread. If the call comes from the event loop thread, it schedules the callable
    to be run in the default executor and returns the corresponding Future. If the call comes from
    any other thread, the callable is run directly.
    """

    @wraps(func, updated=())
    def wrapper(*args, **kwargs):
        if current_thread() is event_loop_thread:
            event_loop = get_event_loop()
            callback = partial(func, *args, **kwargs)
            return event_loop.run_in_executor(None, callback)
        else:
            return func(*args, **kwargs)

    assert not iscoroutinefunction(func), 'Cannot wrap coroutine functions as blocking callables'
    event_loop_thread = main_thread()
    return wrapper


def asynchronous(func: Callable[..., Any]):
    """
    Wraps a callable so that it is guaranteed to be called in the event loop.
    If it returns a coroutine or a Future and the call came from another thread, the coroutine
    or Future is first resolved before returning the result to the caller.
    """

    @wraps(func, updated=())
    def wrapper(*args, **kwargs):
        @coroutine
        def callback():
            try:
                retval = func(*args, **kwargs)
                if iscoroutine(retval) or isinstance(retval, Future):
                    retval = yield from retval
            except Exception as e:
                f.set_exception(e)
            except BaseException as e:  # pragma: no cover
                f.set_exception(e)
                raise
            else:
                f.set_result(retval)

        if current_thread() is event_loop_thread:
            return func(*args, **kwargs)
        else:
            f = Future()
            event_loop.call_soon_threadsafe(async, callback())
            return f.result()

    event_loop = get_event_loop()
    event_loop_thread = main_thread()
    return wrapper


def wrap_blocking_api(cls: type, methods: Container[str]):
    """
    Returns a subclass of the given class with all its specified methods wrapped as coroutines
    for consumption asynchronous code.
    """

    wrapped_methods = {method: synchronous(getattr(cls, method)) for method in methods}
    return type('Wrapped' + cls.__name__, (cls,), wrapped_methods)


def wrap_async_api(cls: type, methods: Container[str]):
    """
    Returns a subclass of the given class with all its specified methods wrapped for
    consumption by threaded code.
    """

    wrapped_methods = {method: asynchronous(getattr(cls, method)) for method in methods}
    return type('Wrapped' + cls.__name__, (cls,), wrapped_methods)
