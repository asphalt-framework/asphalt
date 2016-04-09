import concurrent.futures
from asyncio.events import AbstractEventLoop
from asyncio.tasks import ensure_future
from collections import Callable
from inspect import isawaitable
from threading import get_ident, main_thread, Thread
from typing import Optional

from typeguard import check_argument_types

__all__ = ('set_event_loop', 'get_event_loop', 'is_event_loop_thread')

_event_loop = None
_event_loop_thread_id = main_thread().ident


def set_event_loop(loop: AbstractEventLoop, thread: Thread = None) -> None:
    """
    Mark the current event loop instance and thread to be used by Asphalt applications.

    This is necessary in order for :func:`~asphalt.core.concurrency.async.call_async` and
     :func:`~is_event_loop_thread` to work.

    :param loop: the event loop that will run the Asphalt application
    :param thread: thread the event loop runs in (omit to use the current thread)

    """
    global _event_loop, _event_loop_thread_id
    assert check_argument_types()

    _event_loop = loop
    _event_loop_thread_id = thread.ident if thread else get_ident()


def get_event_loop() -> Optional[AbstractEventLoop]:
    """
    Return the current event loop, as set by :func:`~set_event_loop`.

    Users are discouraged from using this function, and should prefer
    :func:`asyncio.get_event_loop` instead.

    """
    return _event_loop


def is_event_loop_thread() -> bool:
    """
    Return ``True`` if the current thread is the event loop thread.

    .. seealso:: :func:`~set_event_loop`

    """
    return get_ident() == _event_loop_thread_id


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
            if isawaitable(retval):
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
