from collections import defaultdict
from typing import Dict, Callable, Any, Sequence
from enum import Enum

from .util import qualified_name, asynchronous

__all__ = 'CallbackPriority', 'Event', 'ListenerHandle', 'EventSourceMixin'


class CallbackPriority(Enum):
    first = 1
    neutral = 2
    last = 3


class Event:
    __slots__ = 'source', 'name'

    def __init__(self, source: 'EventSourceMixin', name: str):
        self.source = source
        self.name = name


class ListenerHandle:
    __slots__ = 'event_name', 'callback', 'args', 'kwargs', 'priority'

    def __init__(self, event_name: str, callback: Callable[[Event], Any],
                 args: Sequence[Any], kwargs: Dict[str, Any], priority: CallbackPriority):
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


class EventSourceMixin:
    __slots__ = '_event_classes', '_listener_handles'

    def __init__(self, event_classes: Dict[str, Any], *args, **kwargs):
        self._event_classes = event_classes
        self._listener_handles = defaultdict(list)
        super().__init__(*args, **kwargs)

    @asynchronous
    def add_listener(self, event_name: str, callback: Callable[[Any], Any],
                     args: Sequence[Any]=(), kwargs: Dict[str, Any]=None, *,
                     priority: CallbackPriority=CallbackPriority.neutral):
        if event_name not in self._event_classes:
            raise LookupError('no such event type registered: {}'.format(event_name))

        handle = ListenerHandle(event_name, callback, args, kwargs or {}, priority)
        handles = self._listener_handles[event_name]
        handles.append(handle)
        handles.sort()
        return handle

    @asynchronous
    def remove_listener(self, handle: ListenerHandle):
        try:
            self._listener_handles[handle.event_name].remove(handle)
        except (KeyError, ValueError):
            raise LookupError('listener not found') from None

    @asynchronous
    def fire_event(self, event_name: str, *args, **kwargs):
        event_class = self._event_classes.get(event_name)
        if event_class is None:
            raise LookupError('no such event type registered: {}'.format(event_name))

        event = event_class(self, event_name, *args, **kwargs)
        for handle in self._listener_handles[event_name]:
            retval = handle.callback(event, *handle.args, **handle.kwargs)
            if retval is not None:
                yield from retval
