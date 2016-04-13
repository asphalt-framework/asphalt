from typing import Dict, Callable, Any, Sequence, Union, Iterable

from typeguard import check_argument_types

from asphalt.core.util import qualified_name

__all__ = ('Event', 'EventListener', 'EventSource')


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

    def remove(self):
        """Remove this listener from its event source."""
        self.source.remove_listener(self)

    def __repr__(self):
        return ('{0.__class__.__name__}(topics={0.topics!r}, callback={1}, args={0.args!r}, '
                'kwargs={0.kwargs!r})'.format(self, qualified_name(self.callback)))


class EventSource:
    """A mixin class that provides support for dispatching and listening to events."""

    __slots__ = '_topics'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._topics = {}

    def _register_topics(self, topics: Dict[str, Any]):
        """
        Register a number of supported topics and their respective event classes.

        :param topics: a dictionary of topic -> event class

        """
        for topic, event_class in topics.items():
            self._topics[topic] = {'event_class': event_class, 'listeners': []}

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
            if topic not in self._topics:
                raise LookupError('no such topic registered: {}'.format(topic))

        handle = EventListener(self, topics, callback, args, kwargs or {})
        for topic in topics:
            self._topics[topic]['listeners'].append(handle)

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
                self._topics[topic]['listeners'].remove(handle)
        except (KeyError, ValueError):
            raise LookupError('listener not found') from None

    async def dispatch(self, event: Union[str, Event], *args, **kwargs):
        """
        Dispatch an event, optionally constructing one first.

        This method has two forms: dispatch(``event``) and dispatch(``topic``, ``*args``,
        ``**kwargs``). The former dispatches an existing event object while the latter
        instantiates one, using this object as the source. Any extra positional and keyword
        arguments are passed directly to the event class constructor.

        Any exceptions raised by the listener callbacks are passed
        through to the caller.

        :param event: an :class:`~asphalt.core.event.Event` instance or an event topic
        :raises LookupError: if the topic has not been registered in this event source

        """
        assert check_argument_types()
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
                await retval
