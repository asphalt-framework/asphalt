from typing import Dict, Callable, Any, Sequence, Union

from .util import qualified_name, asynchronous

__all__ = 'Event', 'EventListener', 'EventSource'


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

    __slots__ = 'source', 'topic', 'callback', 'args', 'kwargs'

    def __init__(self, source: 'EventSource', topic: str, callback: Callable[[Event], Any],
                 args: Sequence[Any], kwargs: Dict[str, Any]):
        self.source = source
        self.topic = topic
        self.callback = callback
        self.args = args
        self.kwargs = kwargs

    def remove(self):
        """Removes this listener from its event source."""

        self.source.remove_listener(self)

    def __repr__(self):
        return ('{0.__class__.__name__}(topic={0.topic!r}, callback={1}, args={0.args!r}, '
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
                     args: Sequence[Any]=(), kwargs: Dict[str, Any]=None) -> EventListener:
        """
        Starts listening to events specified by ``topic``. The callback (which can be
        a coroutine function) will be called with a single argument (an :class:`Event` instance).
        The exact event class used depends on the event class mappings given to the constructor.

        :param topic: the topic to listen to
        :param callback: a callable to call with the event object when the event is dispatched
        :param args: positional arguments to call the callback with (in addition to the event)
        :param kwargs: keyword arguments to call the callback with
        :return: a listener handle which can be used with :meth:`remove_listener` to unlisten
        :raises LookupError: if the named event has not been registered in this event source
        """

        if topic not in self._topics:
            raise LookupError('no such topic registered: {}'.format(topic))

        handle = EventListener(self, topic, callback, args, kwargs or {})
        handles = self._topics[topic]['listeners']
        handles.append(handle)
        return handle

    @asynchronous
    def remove_listener(self, handle: EventListener):
        """
        Removes an event listener previously added via :meth:`add_listener`.

        :param handle: the listener handle returned from :meth:`add_listener`
        :raises LookupError: if the handle was not found among the registered listeners
        """

        try:
            self._topics[handle.topic]['listeners'].remove(handle)
        except (KeyError, ValueError):
            raise LookupError('listener not found') from None

    @asynchronous
    def dispatch(self, event: Union[str, Event], *args, **kwargs):
        """
        Dispatches an event, optionally constructing one first.

        This method has two forms: dispatch(``event``) and
        dispatch(``topic``, ``*args``, ``**kwargs``).
        The former dispatches an existing event object while the latter instantiates one, using
        this object as the source. Any extra positional and keyword arguments are passed directly
        to the event class constructor.

        Any exceptions raised by the listener callbacks are passed through to the caller.

        :param event: an :class:`~asphalt.core.event.Event` instance or an event topic
        :raises LookupError: if the topic has not been registered in this event source
        """

        topic = event.topic if isinstance(event, Event) else event
        try:
            registration = self._topics[topic]
        except KeyError:
            raise LookupError('no such topic registered: {}'.format(topic)) from None

        if isinstance(event, Event):
            assert not args and not kwargs, 'passing extra arguments makes no sense here'
            assert isinstance(event, registration['event_class']), 'event class mismatch'
        else:
            event_class = registration['event_class']
            event = event_class(self, topic, *args, **kwargs)

        for listener in list(registration['listeners']):
            retval = listener.callback(event, *listener.args, **listener.kwargs)
            if retval is not None:
                yield from retval
