from asyncio import async, coroutine, Task
from typing import Dict, Callable, Any, Sequence

from .util import qualified_name, asynchronous

__all__ = 'Event', 'ListenerHandle', 'EventSource'


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


class ListenerHandle:
    """A handle that can be used to remove an event listener from its :class:`EventSource`."""

    __slots__ = 'topic', 'callback', 'args', 'kwargs'

    def __init__(self, topic: str, callback: Callable[[Event], Any],
                 args: Sequence[Any], kwargs: Dict[str, Any]):
        self.topic = topic
        self.callback = callback
        self.args = args
        self.kwargs = kwargs

    def __repr__(self):
        return ('ListenerHandle(topic={0.topic!r}, callback={1}, args={0.args!r}, '
                'kwargs={0.kwargs!r})'.
                format(self, qualified_name(self.callback)))


class EventSource:
    """A mixin class that provides support for dispatching and listening to events."""

    __slots__ = '_topics'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._topics = {}

    def _register_topics(self, topics: Dict[str, Any]):
        """
        Registers a number of supported topics and their respective event classes

        :param topics: a dictionary of topic -> event class
        """

        for topic, event_class in topics.items():
            self._topics[topic] = {'event_class': event_class, 'listeners': []}

    @asynchronous
    def add_listener(self, topic: str, callback: Callable[[Any], Any],
                     args: Sequence[Any]=(), kwargs: Dict[str, Any]=None) -> ListenerHandle:
        """
        Starts listening to events specified by ``topic``. The callback (which can be
        a coroutine function) will be called with a single argument (an :class:`Event` instance).
        The exact event class used depends on the event class mappings given to the constructor.

        :param topic: the topic to listen to
        :param callback: a callable to call with the event object when the event is dispatched
        :param args: positional arguments to call the callback with (in addition to the event)
        :param kwargs: keyword arguments to call the callback with
        :return: a listener handle which can be used with :meth:`remove_listener` to unlisten
        :raises ValueError: if the named event has not been registered in this event source
        """

        if topic not in self._topics:
            raise ValueError('no such topic registered: {}'.format(topic))

        handle = ListenerHandle(topic, callback, args, kwargs or {})
        handles = self._topics[topic]['listeners']
        handles.append(handle)
        return handle

    @asynchronous
    def remove_listener(self, handle: ListenerHandle):
        """
        Removes an event listener previously added via :meth:`add_listener`.

        :param handle: the listener handle returned from :meth:`add_listener`
        :raises ValueError: if the handle was not found among the registered listeners
        """

        try:
            self._topics[handle.topic]['listeners'].remove(handle)
        except (KeyError, ValueError):
            raise ValueError('listener not found') from None

    @asynchronous
    def dispatch(self, topic: str, *args, **kwargs) -> Task:
        """
        Instantiates an event matching the given topic and calls all the listeners in a separate
        task. Any extra positional and keyword arguments are passed directly to the event class
        constructor.

        :param topic: the topic
        :return: a Task that completes when all the event listeners have been called
        :raises ValueError: if the named event has not been registered in this event source
        """

        if topic not in self._topics:
            raise ValueError('no such topic registered: {}'.format(topic))

        # Run call_listeners() in a separate task to avoid arbitrary exceptions from listeners
        event_class = self._topics[topic]['event_class']
        event = event_class(self, topic, *args, **kwargs)
        return async(self._dispatch(event))

    @coroutine
    def _dispatch(self, event: Event):
        for handle in self._topics[event.topic]['listeners']:
            retval = handle.callback(event, *handle.args, **handle.kwargs)
            if retval is not None:
                yield from retval
