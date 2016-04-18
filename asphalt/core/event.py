import re
from asyncio import ensure_future, Future
from asyncio.queues import Queue
from collections import defaultdict
from inspect import isawaitable
from typing import Dict, Callable, Any, Sequence, Union, Iterable, Tuple

from asyncio_extras.asyncyield import yield_async
from asyncio_extras.generator import async_generator
from typeguard import check_argument_types

from asphalt.core.util import qualified_name

__all__ = ('Event', 'EventListener', 'register_topic', 'EventSource', 'wait_event',
           'stream_events')


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

    :ivar Event event: the event
    :ivar Sequence[Tuple[EventListener, Exception]]: a sequence containing tuples of
        (listener, exception) for each exception that was raised by a listener callback
    """

    def __init__(self, event: Event, exceptions: Sequence[Tuple[EventListener, Exception]]):
        super().__init__('error dispatching event')
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

    __slots__ = '_listeners'

    # Provided in subclasses using @register_topic(...)
    _eventsource_topics = {}  # type: Dict[str, Callable[[Event], Any]]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._listeners = defaultdict(list)

    def add_listener(self, topics: Union[str, Iterable[str]], callback: Callable,
                     args: Sequence=(), kwargs: Dict[str, Any]=None) -> EventListener:
        """
        Start listening to events specified by ``topic``.

        The callback (which can be a coroutine function) will be called with a single argument (an
        :class:`Event` instance). The exact event class used depends on the event class mappings
        given to the constructor.

        :param topics: the topic(s) to listen to
        :param callback: a callable to call with the event object when the event is dispatched
        :param args: positional arguments to call the callback with (in addition to the event)
        :param kwargs: keyword arguments to call the callback with
        :return: a listener handle which can be used with :meth:`remove_listener` to unlisten
        :raises LookupError: if the named event has not been registered in this event source

        """
        assert check_argument_types()
        topics = (topics,) if isinstance(topics, str) else tuple(topics)
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

    async def dispatch_event(self, event: Union[str, Event], *args, **kwargs) -> None:
        """
        Dispatch an event, optionally constructing one first.

        This method has two forms: dispatch(``event``) and dispatch(``topic``, ``*args``,
        ``**kwargs``). The former dispatches an existing event object while the latter
        instantiates one, using this object as the source. Any extra positional and keyword
        arguments are passed directly to the constructor of the event class.

        All listeners are always called. If any event listener raises an exception, an
        :class:`EventDispatchError` is then raised, containing the listeners and the exceptions
        they raised.

        :param event: an :class:`~asphalt.core.event.Event` instance or an event topic
        :raises LookupError: if the topic has not been registered in this event source
        :raises EventDispatchError: if any of the listener callbacks raises an exception

        """
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

        futures, exceptions = [], []
        for listener in list(self._listeners[topic]):
            try:
                retval = listener.callback(event, *listener.args, **listener.kwargs)
            except Exception as e:
                exceptions.append((listener, e))
            else:
                if isawaitable(retval):
                    future = ensure_future(retval)
                    futures.append((listener, future))

        # For any callbacks that returned awaitables, wait for their completion and collect any
        # exceptions they raise
        for listener, future in futures:
            try:
                await future
            except Exception as e:
                exceptions.append((listener, e))

        if exceptions:
            raise EventDispatchError(event, exceptions)


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
