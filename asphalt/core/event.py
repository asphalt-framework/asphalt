from asyncio import async, coroutine, Task
from collections import defaultdict
from typing import Dict, Callable, Any, Sequence
from enum import Enum

from .util import qualified_name, asynchronous

__all__ = 'ListenerPriority', 'Event', 'ListenerHandle', 'EventSource'


class ListenerPriority(Enum):
    first = 1
    neutral = 2
    last = 3


class Event:
    """
    The base class for all events.

    :ivar source: the object that (originally) fired this event
    :ivar name: the event name
    """

    __slots__ = 'source', 'name'

    def __init__(self, source: 'EventSource', name: str):
        self.source = source
        self.name = name


class ListenerHandle:
    __slots__ = 'event_name', 'callback', 'args', 'kwargs', 'priority'

    def __init__(self, event_name: str, callback: Callable[[Event], Any],
                 args: Sequence[Any], kwargs: Dict[str, Any], priority: ListenerPriority):
        self.event_name = event_name
        self.callback = callback
        self.args = args
        self.kwargs = kwargs
        self.priority = priority

    def __lt__(self, other):
        if isinstance(other, ListenerHandle):
            return (self.event_name, self.priority.value) < (self.event_name, other.priority.value)
        return NotImplemented

    def __repr__(self):
        return ('ListenerHandle(event_name={0.event_name!r}, callback={1}, args={0.args!r}, '
                'kwargs={0.kwargs!r}, priority={0.priority.name})'.
                format(self, qualified_name(self.callback)))


class EventSource:
    """
    A mixin class that provides support for firing and listening to events.
    It requires a mapping of supported event named to their respective Event classes as its
    first argument.

    :param event_classes: a mapping of event name -> event class
    """

    __slots__ = '_event_classes', '_listener_handles'

    def __init__(self, event_classes: Dict[str, Any], *args, **kwargs):
        self._event_classes = event_classes
        self._listener_handles = defaultdict(list)
        super().__init__(*args, **kwargs)

    @asynchronous
    def add_listener(self, event_name: str, callback: Callable[[Any], Any],
                     args: Sequence[Any]=(), kwargs: Dict[str, Any]=None, *,
                     priority: ListenerPriority=ListenerPriority.neutral) -> ListenerHandle:
        """
        Starts listening to the events specified by ``event_name``. The callback (which can be
        a coroutine function) will be called with a single argument (an :class:`Event` instance).
        The exact event class used depends on the event class mappings given to the constructor.

        It is possible to prioritize the listener to be called among the first or last in the
        group by specifying an alternate :class:`ListenerPriority` value as ``priority``.

        :param event_name: the event name to listen to
        :param callback: a callable to call with the event object when the event is fired
        :param args: positional arguments to call the callback with (in addition to the event)
        :param kwargs: keyword arguments to call the callback with
        :param priority: priority of the callback among other listeners of the same event
        :return: a listener handle which can be used with :meth:`remove_listener` to unlisten
        :raises ValueError: if the named event has not been registered in this event source
        """

        if event_name not in self._event_classes:
            raise ValueError('no such event registered: {}'.format(event_name))

        handle = ListenerHandle(event_name, callback, args, kwargs or {}, priority)
        handles = self._listener_handles[event_name]
        handles.append(handle)
        handles.sort()
        return handle

    @asynchronous
    def remove_listener(self, handle: ListenerHandle):
        """
        Removes an event listener previously added via :meth:`add_listener`.

        :param handle: the listener handle returned from :meth:`add_listener`
        :raises ValueError: if the handle was not found among the registered listeners
        """

        try:
            self._listener_handles[handle.event_name].remove(handle)
        except (KeyError, ValueError):
            raise ValueError('listener not found') from None

    @asynchronous
    def fire_event(self, event_name: str, *args, **kwargs) -> Task:
        """
        Instantiates an event matching the given registered event name and calls all the
        listeners in a separate task.

        :param event_name: the event name identifying the event class to use
        :param args: positional arguments to pass to the event class constructor
        :param kwargs: keyword arguments to pass to the event class constructor
        :return: a Task that completes when all the event listeners have been called
        :raises ValueError: if the named event has not been registered in this event source
        """

        event_class = self._event_classes.get(event_name)
        if event_class is None:
            raise ValueError('no such event registered: {}'.format(event_name))

        # Run call_listeners() in a separate task to avoid arbitrary exceptions from listeners
        event = event_class(self, event_name, *args, **kwargs)
        return async(self._fire_event(event))

    @coroutine
    def _fire_event(self, event: Event):
        for handle in self._listener_handles[event.name]:
            retval = handle.callback(event, *handle.args, **handle.kwargs)
            if retval is not None:
                yield from retval
