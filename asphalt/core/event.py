import logging
import re
from asyncio import Future, get_event_loop, Queue
from collections import defaultdict
from inspect import isawaitable, iscoroutine
from traceback import format_exception
from typing import Dict, Callable, Any, Sequence, Union, Iterable, Tuple, Optional

from asyncio_extras.asyncyield import yield_async
from asyncio_extras.generator import async_generator
from typeguard import check_argument_types

from asphalt.core.util import qualified_name

__all__ = ('Event', 'EventListener', 'register_topic', 'EventSource', 'wait_event',
           'stream_events')

logger = logging.getLogger(__name__)


class Event:
    """
    The base class for all events.

    :ivar EventSource source: the event source where this event originated from
    :ivar str topic: the topic
    """

    __slots__ = 'source', 'topic'

    def __init__(self, source: 'EventSource', topic: str):
        self.source = source
        self.topic = topic

    def __repr__(self):
        return '{0.__class__.__name__}(source={0.source!r}, topic={0.topic!r})'.format(self)


class EventListener:
    """A handle that can be used to remove an event listener from its :class:`EventSource`."""

    __slots__ = 'source', 'topics', 'callback', 'args', 'kwargs'

    def __init__(self, source: 'EventSource', topics: Sequence[str], callback: Callable,
                 args: Sequence, kwargs: Dict[str, Any]):
        assert check_argument_types()
        self.source = source
        self.topics = topics
        self.callback = callback
        self.args = args
        self.kwargs = kwargs

    def remove(self) -> None:
        """Remove this listener from its event source."""
        self.source.remove_listener(self)

    def __repr__(self):
        return ('{0.__class__.__name__}(topics={0.topics!r}, callback={1}, args={0.args!r}, '
                'kwargs={0.kwargs!r})'.format(self, qualified_name(self.callback)))


class EventDispatchError(Exception):
    """
    Raised when one or more event listener callback raises an exception.

    The tracebacks of all the exceptions are displayed in the exception message.

    :ivar Event event: the event
    :ivar exceptions: a sequence containing tuples of (listener, exception) for each exception that
        was raised by a listener callback
    :vartype exceptions: Sequence[Tuple[EventListener, Exception]]
    """

    def __init__(self, event: Event, exceptions: Sequence[Tuple[EventListener, Exception]]):
        message = '-------------------------------\n'.join(
            ''.join(format_exception(type(exc), exc, exc.__traceback__)) for _, exc in exceptions)
        super().__init__('error dispatching event:\n' + message)
        self.event = event
        self.exceptions = exceptions


def register_topic(name: str, event_class: type = Event):
    """
    Return a class decorator that registers an event topic on the given class.

    A subclass may override an event topic by re-registering it with an event class that is a
    subclass of the previously registered event class. Attempting to override the topic with an
    incompatible class will raise a :exc:`TypeError`.

    :param name: name of the topic (must consist of alphanumeric characters and ``_``)
    :param event_class: the event class associated with this topic

    """
    def wrapper(cls: type):
        if not isinstance(cls, type) or not issubclass(cls, EventSource):
            raise TypeError('cls must be a subclass of EventSource')

        topics = cls.__dict__.get('_eventsource_topics')
        if topics is None:
            # Collect all the topics from superclasses
            topics = cls._eventsource_topics = {}
            for supercls in cls.__mro__[1:]:
                supercls_topics = getattr(supercls, '_eventsource_topics', {})
                topics.update(supercls_topics)

        if name in topics and not issubclass(event_class, topics[name]):
            existing_classname = qualified_name(topics[name])
            new_classname = qualified_name(event_class)
            raise TypeError('cannot override event class for topic "{}" -- event class {} is not '
                            'a subclass of {}'.format(name, new_classname, existing_classname))

        topics[name] = event_class
        return cls

    assert check_argument_types()
    assert re.match('[a-z0-9_]+', name), 'invalid characters in topic name'
    assert issubclass(event_class, Event), 'event_class must be a subclass of Event'
    return wrapper


class EventSource:
    """A mixin class that provides support for dispatching and listening to events."""

    __slots__ = '__listeners'

    # Provided in subclasses using @register_topic(...)
    _eventsource_topics = {}  # type: Dict[str, Callable[[Event], Any]]

    @property
    def _listeners(self):
        try:
            return self.__listeners
        except AttributeError:
            self.__listeners = defaultdict(list)
            return self.__listeners

    def add_listener(self, topics: Union[str, Iterable[str]], callback: Callable,
                     args: Sequence = (), kwargs: Dict[str, Any] = None) -> EventListener:
        """
        Start listening to events specified by ``topic``.

        The callback (which can be a coroutine function) will be called with a single argument (an
        :class:`Event` instance). The exact event class used depends on the event class mappings
        given to the constructor.

        :param topics: either a comma separated list or an iterable of topic(s) to listen to
        :param callback: a callable to call with the event object when the event is dispatched
        :param args: positional arguments to call the callback with (in addition to the event)
        :param kwargs: keyword arguments to call the callback with
        :return: a listener handle which can be used with :meth:`remove_listener` to unlisten
        :raises LookupError: if the named event has not been registered in this event source

        """
        assert check_argument_types()

        if isinstance(topics, str):
            topics = tuple(topic.strip() for topic in topics.split(','))
        else:
            topics = tuple(topics)

        for topic in topics:
            if topic not in self._eventsource_topics:
                raise LookupError('no such topic registered: {}'.format(topic))

        handle = EventListener(self, topics, callback, args, kwargs or {})
        for topic in topics:
            self._listeners[topic].append(handle)

        return handle

    def remove_listener(self, handle: EventListener):
        """
        Remove an event listener previously added via :meth:`add_listener`.

        :param handle: the listener handle returned from :meth:`add_listener`
        :raises LookupError: if the handle was not found among the registered listeners

        """
        assert check_argument_types()
        try:
            for topic in handle.topics:
                self._listeners[topic].remove(handle)
        except (KeyError, ValueError):
            raise LookupError('listener not found') from None

    def dispatch_event(self, event: Union[str, Event], *args, return_future: bool = False,
                       **kwargs) -> Optional[Future]:
        """
        Dispatch an event, optionally constructing one first.

        This method has two forms: dispatch_event(``event``) and dispatch_event(``topic``,
        ``*args``, ``**kwargs``). The former dispatches an existing event object while the latter
        instantiates one, using this object as the source. Any extra positional and keyword
        arguments are passed directly to the constructor of the event class.

        :param event: an event instance or an event topic
        :param return_future:
            If ``True``, return a :class:`~asyncio.Future` that completes when all the listener
            callbacks have been processed. If any one of them raised an exception, the future will
            have an :exc:`~asphalt.core.event.EventDispatchError` exception set in it which
            contains all of the exceptions raised in the callbacks.

            If set to ``False``, then ``None`` will be returned, and any exceptions raised in
            listener callbacks will be logged instead.
        :raises LookupError: if the topic has not been registered in this event source
        :returns: a future or ``None``, depending on the ``return_future`` argument

        """
        async def do_dispatch():
            futures, exceptions = [], []
            for listener in listeners:
                try:
                    retval = listener.callback(event, *listener.args, **listener.kwargs)
                except Exception as e:
                    exceptions.append((listener, e))
                    if not return_future:
                        logger.exception('uncaught exception in event listener')
                else:
                    if isawaitable(retval):
                        if iscoroutine(retval):
                            retval = loop.create_task(retval)

                        futures.append((listener, retval))

            # For any callbacks that returned awaitables, wait for their completion and collect any
            # exceptions they raise
            for listener, awaitable in futures:
                try:
                    await awaitable
                except Exception as e:
                    exceptions.append((listener, e))
                    if not return_future:
                        logger.exception('uncaught exception in event listener')

            if exceptions and return_future:
                raise EventDispatchError(event, exceptions)

        assert check_argument_types()
        topic = event.topic if isinstance(event, Event) else event
        try:
            event_class = self._eventsource_topics[topic]
        except KeyError:
            raise LookupError('no such topic registered: {}'.format(topic)) from None

        if isinstance(event, Event):
            assert not args and not kwargs, 'passing extra arguments makes no sense here'
            assert isinstance(event, event_class), 'event class mismatch'
        else:
            event = event_class(self, topic, *args, **kwargs)

        listeners = list(self._listeners[topic])
        if listeners:
            loop = get_event_loop()
            future = loop.create_task(do_dispatch())
            return future if return_future else None
        elif return_future:
            # The event has no listeners, so skip the task creation and return an empty Future
            future = Future()
            future.set_result(None)
            return future
        else:
            return None


async def wait_event(source: EventSource, topics: Union[str, Iterable[str]]) -> Event:
    """
    Listen to the given topic(s) on the event source and return the next received event.

    :param source: an event source
    :param topics: topic or topics to listen to
    :return: the received event object

    """
    future = Future()
    listener = source.add_listener(topics, future.set_result)
    try:
        return await future
    finally:
        listener.remove()


@async_generator
async def stream_events(source: EventSource, topics: Union[str, Iterable[str]]):
    """
    Generate event objects to the consumer as they're dispatched.

    This function is meant for use with ``async for``.

    :param source: an event source
    :param topics: topic or topics to listen to

    """
    queue = Queue()
    listener = source.add_listener(topics, queue.put)
    try:
        while True:
            event = await queue.get()
            await yield_async(event)
    finally:
        listener.remove()
