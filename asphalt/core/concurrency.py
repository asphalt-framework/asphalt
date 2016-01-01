from asyncio import AbstractEventLoop, iscoroutinefunction, coroutine, iscoroutine, async, Future
from functools import wraps, partial
from threading import Thread, main_thread, get_ident
import concurrent.futures

from typeguard import check_argument_types
from typing import Callable, Any

__all__ = ('set_event_loop', 'is_event_loop_thread', 'blocking', 'asynchronous', 'stop_event_loop')

event_loop = None
event_loop_thread_id = main_thread().ident


def set_event_loop(loop: AbstractEventLoop, thread: Thread=None):
    """
    Set the event loop to be used by Asphalt applications.

    This is necessary in order for :func:`blocking` and :func:`asynchronous` to work.

    :param loop: the event loop that will run the Asphalt application
    :param thread: thread the event loop runs in (omit to use the current thread)

    """
    global event_loop, event_loop_thread_id
    assert check_argument_types()

    event_loop = loop
    event_loop_thread_id = thread.ident if thread else get_ident()


def is_event_loop_thread() -> bool:
    """
    Return ``True`` if the current thread is the event loop thread.

    .. seealso:: :func:`set_event_loop`

    """
    return get_ident() == event_loop_thread_id


def blocking(func: Callable):
    """
    Return a wrapper that guarantees that the target callable will be run in a thread other than
    the event loop thread.

    If the call comes from the event loop thread, it schedules the callable
    to be run in the default executor and returns the corresponding Future. If the call comes from
    any other thread, the callable is run directly.

    """
    @wraps(func, updated=())
    def wrapper(*args, **kwargs):
        if is_event_loop_thread():
            callback = partial(func, *args, **kwargs)
            return event_loop.run_in_executor(None, callback)
        else:
            return func(*args, **kwargs)

    assert check_argument_types()
    assert not iscoroutinefunction(func), 'Cannot wrap coroutine functions as blocking callables'
    return wrapper


def asynchronous(func: Callable[..., Any]):
    """
    Wrap a callable so that it is guaranteed to be called in the event loop.

    If it returns a coroutine or a :class:`~asyncio.Future` and the call came from another thread,
    the coroutine or :class:`~asyncio.Future` is first resolved before returning the result to the
    caller.

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

        if is_event_loop_thread():
            return func(*args, **kwargs)
        else:
            f = concurrent.futures.Future()
            event_loop.call_soon_threadsafe(async, callback())
            return f.result()

    assert check_argument_types()
    return wrapper


def stop_event_loop():
    """
    Schedule the current event loop to stop on the next iteration.

    This function is the only supported way to stop the event loop from a non-eventloop thread.

    """
    event_loop.call_soon_threadsafe(event_loop.stop)
