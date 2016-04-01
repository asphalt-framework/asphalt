import concurrent.futures
import gc
import inspect
from asyncio.tasks import ensure_future
from concurrent.futures import Executor
from functools import wraps, partial
from inspect import iscoroutinefunction, isawaitable
from threading import Event
from typing import Optional, Callable

from asphalt.core.concurrency.eventloop import is_event_loop_thread, get_event_loop
from typeguard import check_argument_types

__all__ = ('executor', 'blocking', 'asynchronous')


class _Switch:
    __slots__ = 'executor', 'exited'

    def __init__(self, executor: Optional[Executor]):
        self.executor = executor
        self.exited = False

    def __aenter__(self):
        # This is run in the event loop thread
        assert is_event_loop_thread(), 'already running in a worker thread'
        return self

    def __await__(self):
        def exec_when_ready():
            event.wait()
            coro.send(None)

        if self.exited:
            # This is run in the worker thread
            yield
        else:
            # This is run in the event loop thread
            loop = get_event_loop()
            previous_frame = inspect.currentframe().f_back
            coro = next(obj for obj in gc.get_referrers(previous_frame.f_code)
                        if inspect.iscoroutine(obj))
            event = Event()
            future = loop.run_in_executor(self.executor, exec_when_ready)
            future._blocking = True
            loop.call_soon(event.set)
            yield future

    def __aexit__(self, exc_type, exc_val, exc_tb):
        # This is run in the worker thread
        self.exited = True
        return self


def executor(executor: Executor=None):
    """
    Return an asynchronous context manager that runs the block in an executor.

    If no executor is given, the event loop's default executor is used.
    Otherwise, the executor must be a thread pool executor and executors must not be nested.

    :param executor: the executor in which to run the ``with`` block

    """
    assert check_argument_types()
    return _Switch(executor)


def blocking(func: Callable, *, executor: Executor = None) -> Callable:
    """
    Return a wrapper that guarantees that the target callable will be run in a worker thread.

    If the wrapper is called in the event loop thread, it schedules the wrapped callable to be run
    in the default executor and returns the corresponding :class:`asyncio.futures.Future`.
    If the call comes from any other thread, the callable is run directly.

    :param func: the target callable to wrap
    :param executor: the executor that will run the target callable

    """
    @wraps(func, updated=())
    def wrapper(*args, **kwargs):
        if is_event_loop_thread():
            callback = partial(func, *args, **kwargs)
            return get_event_loop().run_in_executor(executor, callback)
        else:
            return func(*args, **kwargs)

    assert check_argument_types()
    assert not iscoroutinefunction(func), 'Cannot wrap coroutine functions as blocking callables'
    return wrapper


def asynchronous(func: Callable) -> Callable:
    """
    Wrap a callable so that it is guaranteed to be called in the event loop.

    If it returns a coroutine or a :class:`~asyncio.Future` and the call came from another thread,
    the coroutine or :class:`~asyncio.Future` is first resolved before returning the result to the
    caller.

    :param func: the target callable to wrap

    """
    @wraps(func, updated=())
    def wrapper(*args, **kwargs):
        async def callback():
            try:
                retval = func(*args, **kwargs)
                if isawaitable(retval):
                    retval = await retval
            except Exception as e:
                f.set_exception(e)
            except BaseException as e:  # pragma: no cover
                f.set_exception(e)
                raise
            else:
                f.set_result(retval)

        if is_event_loop_thread():
            return func(*args, **kwargs)
        else:
            f = concurrent.futures.Future()
            get_event_loop().call_soon_threadsafe(ensure_future, callback())
            return f.result()

    assert check_argument_types()
    return wrapper
