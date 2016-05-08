import logging
from asyncio import Future, get_event_loop, Queue
from datetime import datetime, timezone
from inspect import isawaitable, iscoroutine, getmembers
from time import time, monotonic
from traceback import format_exception
from types import CoroutineType
from typing import Callable, Any, Sequence, Tuple, Optional
from weakref import WeakKeyDictionary

from asyncio_extras.asyncyield import yield_async
from asyncio_extras.generator import async_generator
from typeguard import check_argument_types

from asphalt.core.util import qualified_name

__all__ = ('Event', 'EventDispatchError', 'Signal', 'wait_event', 'stream_events')

logger = logging.getLogger(__name__)


class Event:
    """
    The base class for all events.

    :param source: the object where this event originated from
    :param topic: the event topic

    :ivar source: the object where this event was dispatched from
    :ivar str topic: the topic
    :ivar float time: event creation time as seconds from the UNIX epoch
    :ivar float monotime: event creation time, as returned by :func:`time.monotonic`
    """

    __slots__ = 'source', 'topic', 'time', 'monotime'

    def __init__(self, source, topic: str):
        self.source = source
        self.topic = topic
        self.time = time()
        self.monotime = monotonic()

    @property
    def utc_timestamp(self) -> datetime:
        """
        Return a timezone aware :class:`~datetime.datetime` corresponding to the ``time`` variable,
        using the UTC timezone.

        """
        return datetime.fromtimestamp(self.time, timezone.utc)

    def __repr__(self):
        return '{0.__class__.__name__}(source={0.source!r}, topic={0.topic!r})'.format(self)


class EventDispatchError(Exception):
    """
    Raised when one or more event listener callbacks raise an exception.

    The tracebacks of all the exceptions are displayed in the exception message.

    :ivar Event event: the event
    :ivar exceptions: a sequence containing tuples of (callback, exception) for each exception that
        was raised by a listener callback
    :vartype exceptions: Sequence[Tuple[Callable, Exception]]
    """

    def __init__(self, event: Event, exceptions: Sequence[Tuple[Callable, Exception]]):
        message = '-------------------------------\n'.join(
            ''.join(format_exception(type(exc), exc, exc.__traceback__)) for _, exc in exceptions)
        super().__init__('error dispatching event:\n' + message)
        self.event = event
        self.exceptions = exceptions


class Signal:
    """
    Declaration of a signal that can be used to dispatch events.

    This is a descriptor that returns itself on class level attribute access and a bound version of
    itself on instance level access. Connecting listeners and dispatching events only works with
    these bound instances.

    Each signal must be assigned to a class attribute, but only once. Assigning the same Signal
    instance to more than one attribute will raise a :exc:`LookupError` on attribute access.

    :param event_class: an event class
    """
    __slots__ = 'event_class', 'source', 'topic', 'listeners', 'bound_signals'

    def __init__(self, event_class: type, *, source=None, topic: str = None):
        assert check_argument_types()
        assert issubclass(event_class, Event), 'event_class must be a subclass of Event'
        self.event_class = event_class
        self.topic = topic
        if source is not None:
            self.source = source
            self.listeners = []
        else:
            self.bound_signals = WeakKeyDictionary()

    def __get__(self, instance, owner) -> 'Signal':
        if instance is None:
            return self

        # Find the attribute this Signal was assigned to
        if self.topic is None:
            attrnames = [attr for attr, value in getmembers(owner, lambda value: value is self)]
            if len(attrnames) > 1:
                raise LookupError('this Signal was assigned to multiple attributes: ' +
                                  ', '.join(attrnames))
            else:
                self.topic = attrnames[0]

        try:
            return self.bound_signals[instance]
        except KeyError:
            bound_signal = Signal(self.event_class, source=instance, topic=self.topic)
            self.bound_signals[instance] = bound_signal
            return bound_signal

    def connect(self, callback: Callable[[Event], Any]) -> Callable[[Event], Any]:
        """
        Connect a callback to this signal.

        Each callable can only be connected once. Duplicate registrations are ignored.

        If you need to pass extra arguments to the callback, you can use :func:`functools.partial`
        to wrap the callable.

        :param callback: a callable that will receive an event object as its only argument.
        :return: the value of ``callback`` argument

        """
        assert check_argument_types()
        if callback not in self.listeners:
            self.listeners.append(callback)

        return callback

    def disconnect(self, callback: Callable) -> None:
        """
        Disconnects the given callback.

        The callback will no longer receive events from this signal.

        No action is taken if the callback is not on the list of listener callbacks.

        :param callback: the callable to remove

        """
        assert check_argument_types()
        try:
            self.listeners.remove(callback)
        except ValueError:
            pass

    def dispatch_event(self, event: Event, *, return_future: bool = False) -> Optional[Future]:
        """
        Dispatch the given event to all listeners.

        Creates a new task in which all listener callbacks are called with the given event as
        the only argument. Coroutine callbacks are converted to their own respective tasks and
        waited for concurrently.

        :param event: the event object to dispatch
        :param return_future:
            If ``True``, return a :class:`~asyncio.Future` that completes when all the listener
            callbacks have been processed. If any one of them raised an exception, the future will
            have an :exc:`~asphalt.core.event.EventDispatchError` exception set in it which
            contains all of the exceptions raised in the callbacks.

            If set to ``False``, then ``None`` will be returned, and any exceptions raised in
            listener callbacks will be logged instead.
        :returns: a future or ``None``, depending on the ``return_future`` argument

        """
        async def do_dispatch():
            futures, exceptions = [], []
            for callback in listeners:
                try:
                    retval = callback(event)
                except Exception as e:
                    exceptions.append((callback, e))
                    if not return_future:
                        logger.exception('uncaught exception in event listener')
                else:
                    if isawaitable(retval):
                        if iscoroutine(retval):
                            retval = loop.create_task(retval)

                        futures.append((callback, retval))

            # For any callbacks that returned awaitables, wait for their completion and collect any
            # exceptions they raise
            for callback, awaitable in futures:
                try:
                    await awaitable
                except Exception as e:
                    exceptions.append((callback, e))
                    if not return_future:
                        logger.exception('uncaught exception in event listener')

            if exceptions and return_future:
                raise EventDispatchError(event, exceptions)

        assert isinstance(event, self.event_class), \
            'event must be of type {}'.format(qualified_name(self.event_class))
        if self.listeners:
            loop = get_event_loop()
            listeners = self.listeners.copy()
            future = loop.create_task(do_dispatch())
            return future if return_future else None
        elif return_future:
            # The event has no listeners, so skip the task creation and return an empty Future
            future = Future()
            future.set_result(None)
            return future
        else:
            return None

    def dispatch(self, *args, return_future: bool = False, **kwargs) -> Optional[Future]:
        """
        Create and dispatch an event.

        This method constructs an event object and then passes it to :meth:`dispatch_event` for
        the actual dispatching.

        :param args: positional arguments to the constructor of the associated event class.
        :param return_future:
            If ``True``, return a :class:`~asyncio.Future` that completes when all the listener
            callbacks have been processed. If any one of them raised an exception, the future will
            have an :exc:`~asphalt.core.event.EventDispatchError` exception set in it which
            contains all of the exceptions raised in the callbacks.

            If set to ``False``, then ``None`` will be returned, and any exceptions raised in
            listener callbacks will be logged instead.
        :returns: a future or ``None``, depending on the ``return_future`` argument

        """
        assert check_argument_types()
        event = self.event_class(self.source, self.topic, *args, **kwargs)
        return self.dispatch_event(event, return_future=return_future)

    def wait_event(self) -> CoroutineType:
        """Shortcut for calling :func:`wait_event` with this signal as the argument."""
        return wait_event(self)

    def stream_events(self, max_queue_size: int = 0):
        """Shortcut for calling :func:`stream_events` with this signal as the argument."""
        return stream_events(self, max_queue_size=max_queue_size)


async def wait_event(*signals: Signal) -> Event:
    """Return the first event dispatched from any of the given signals."""
    future = Future()
    for signal in signals:
        signal.connect(future.set_result)

    try:
        return await future
    finally:
        for signal in signals:
            signal.disconnect(future.set_result)


@async_generator
async def stream_events(*signals: Signal, max_queue_size: int = 0):
    """
    Generate event objects to the consumer as they're dispatched.

    This function is meant for use with ``async for``.

    :param signals: one or more signals to get events from
    :param max_queue_size: maximum size of the queue, after which it will start to drop events

    """
    queue = Queue(max_queue_size)
    for signal in signals:
        signal.connect(queue.put_nowait)

    try:
        while True:
            event = await queue.get()
            await yield_async(event)
    finally:
        for signal in signals:
            signal.disconnect(queue.put_nowait)
