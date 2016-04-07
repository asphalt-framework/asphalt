from asyncio.events import AbstractEventLoop
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
