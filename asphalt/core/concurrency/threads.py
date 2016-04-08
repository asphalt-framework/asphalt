import concurrent.futures
import gc
import inspect
from asyncio.futures import Future
from asyncio.tasks import ensure_future
from concurrent.futures import Executor
from functools import wraps, partial
from threading import Event
from typing import Optional, Callable, Union

from typeguard import check_argument_types

from asphalt.core.concurrency.eventloop import is_event_loop_thread, get_event_loop

__all__ = ('threadpool', 'call_in_thread', 'call_async')


class _ThreadSwitcher:
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
            next(future.__await__())  # Make the future think it's being awaited on
            loop.call_soon(event.set)
            yield future

    def __aexit__(self, exc_type, exc_val, exc_tb):
        # This is run in the worker thread
        self.exited = True
        return self

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if is_event_loop_thread():
                callback = partial(func, *args, **kwargs)
                return get_event_loop().run_in_executor(self.executor, callback)
            else:
                return func(*args, **kwargs)

        assert check_argument_types()
        assert not inspect.iscoroutinefunction(func), \
            'Cannot wrap coroutine functions as blocking callables'
        return wrapper


def threadpool(arg: Union[Executor, Callable] = None):
    """
    Return a decorator/asynchronous context manager that guarantees that the wrapped function or
    ``with``block is run in a worker thread.

    Callables wrapped with this must be used with ``await`` when called in the event loop thread.
    When called in any other thread, the wrapped callables work normally.

    If no executor is given, the event loop's default executor is used.
    Otherwise, the executor must be a PEP 3148 compliant thread pool executor and executors must
    not be nested.

    :param arg: either a callable (when used as a decorator) or an executor in which to run the
        wrapped callable or the ``with`` block (when used as a context manager)

    """
    assert check_argument_types()
    if callable(arg):
        # When used like @threadpool
        return _ThreadSwitcher(None)(arg)
    else:
        # When used like @threadpool(...) or async with threadpool(...)
        return _ThreadSwitcher(arg)


def call_in_thread(func: Callable, *args, executor: Executor = None, **kwargs) -> Future:
    """
    Call the given callable in a worker thread.

    This function is meant for one-off invocations of functions that warrant execution in a worker
    thread because the call would block the event loop for a significant amount of time.

    Calling this function is equivalent to doing ``threadpool(func)(*args, **kwargs)`` or
    ``threadpool(executor)(func)(*args, **kwargs)``.

    :param func: a function
    :param args: positional arguments to call with
    :param executor: the executor to call the function in
    :param kwargs: keyword arguments to call with
    :return: a future that will resolve to the function call's return value

    """
    assert check_argument_types()
    assert is_event_loop_thread(), 'call_in_thread() must be called in the event loop thread'
    callback = partial(func, *args, **kwargs)
    return get_event_loop().run_in_executor(executor, callback)


def call_async(func: Callable, *args, **kwargs):
    """
    Call the given callable in the event loop thread.

    If the call returns an awaitable, it is resolved before returning to the caller.

    :param func: a regular function or a coroutine function
    :param args: positional arguments to call with
    :param kwargs: keyword arguments to call with
    :return: the return value of the function call

    """
    async def callback():
        try:
            retval = func(*args, **kwargs)
            if inspect.isawaitable(retval):
                retval = await retval
        except BaseException as e:
            f.set_exception(e)
        else:
            f.set_result(retval)

    assert check_argument_types()
    assert not is_event_loop_thread(), 'call_async() must be called in a worker thread'
    f = concurrent.futures.Future()
    get_event_loop().call_soon_threadsafe(ensure_future, callback())
    return f.result()
