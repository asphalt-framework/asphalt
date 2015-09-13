from asyncio import coroutine, iscoroutinefunction, iscoroutine, async
from concurrent.futures import Future
from importlib import import_module
from threading import get_ident
from typing import Callable, Any
from functools import wraps, partial

from pkg_resources import EntryPoint, iter_entry_points

__all__ = 'resolve_reference', 'qualified_name', 'synchronous', 'asynchronous', 'PluginContainer'

event_loop = event_loop_thread_id = None


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

    return qualname if module in ('typing', 'builtins') else '{}.{}'.format(module, qualname)


def synchronous(func: Callable[..., Any]):
    """
    Returns a wrapper that guarantees that the target callable will be run in a thread other than
    the event loop thread. If the call comes from the event loop thread, it schedules the callable
    to be run in the default executor and returns the corresponding Future. If the call comes from
    any other thread, the callable is run directly.
    """

    @wraps(func, updated=())
    def wrapper(*args, **kwargs):
        if get_ident() == event_loop_thread_id:
            callback = partial(func, *args, **kwargs)
            return event_loop.run_in_executor(None, callback)
        else:
            return func(*args, **kwargs)

    assert not iscoroutinefunction(func), 'Cannot wrap coroutine functions as blocking callables'
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

        if event_loop_thread_id in (get_ident(), None):
            return func(*args, **kwargs)
        else:
            f = Future()
            event_loop.call_soon_threadsafe(async, callback())
            return f.result()

    return wrapper


class PluginContainer:
    """
    A convenience class for loading entry points on demand.

    :param namespace: a setuptools entry points namespace
    """

    __slots__ = 'namespace', '_entrypoints'

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._entrypoints = {ep.name: ep for ep in iter_entry_points(namespace)}

    def resolve(self, obj):
        """
        If ``obj`` is a textual reference to an object (contains ":"), returns the resolved
        reference using :func:`resolve_reference`. If it is a string of any other kind, loads the
        named entry point in this container's namespace. Otherwise, ``obj`` is returned as is.

        :param obj: an entry point identifier, an object reference or an arbitrary object
        :return: the loaded entry point, resolved object or the unchanged input value
        :raises LookupError: if ``obj`` was a string but the named entry point was not found
        """

        if not isinstance(obj, str):
            return obj
        if ':' in obj:
            return resolve_reference(obj)

        value = self._entrypoints.get(obj)
        if value is None:
            raise LookupError('no such entry point in {}: {}'.format(self.namespace, obj))

        if isinstance(value, EntryPoint):
            value = self._entrypoints[obj] = value.load()

        return value

    def __repr__(self):
        return '{0.__class__.__name__}(namespace={0.namespace!r})'.format(self)
