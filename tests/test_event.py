from asyncio import coroutine
import operator

import pytest

from asphalt.core.event import EventSourceMixin, Event, CallbackPriority, ListenerHandle


class DummyEvent(Event):
    def __init__(self, source, name, *args, **kwargs):
        super().__init__(source, name)
        self.args = args
        self.kwargs = kwargs


class TestListenerHandle:
    @pytest.fixture
    def handle(self):
        return ListenerHandle('foo', lambda: None, (), {}, CallbackPriority.neutral)

    @pytest.mark.parametrize('other, expected', [
        (ListenerHandle('foo', lambda: None, (), {}, CallbackPriority.first), False),
        (ListenerHandle('foo', lambda: None, (), {}, CallbackPriority.last), True)
    ], ids=['other_first', 'other_last'])
    def test_lt(self, handle, other, expected):
        assert (handle < other) == expected

    def test_lt_not_implemented(self, handle):
        exc = pytest.raises(TypeError, operator.lt, handle, 1)
        assert str(exc.value) == 'unorderable types: ListenerHandle() < int()'

    def test_repr(self, handle):
        assert repr(handle) == (
            "ListenerHandle(event_name='foo', "
            "callback=test_event.TestListenerHandle.handle.<locals>.<lambda>, args=(), kwargs={}, "
            "priority=neutral)")


class TestEventSource:
    @pytest.fixture
    def source(self):
        return EventSourceMixin({'event_a': DummyEvent, 'event_b': DummyEvent})

    def test_add_listener(self, source):
        handle = source.add_listener('event_a', lambda: None)
        assert isinstance(handle, ListenerHandle)
        assert handle.event_name == 'event_a'

    def test_add_listener_nonexistent_event(self, source):
        exc = pytest.raises(LookupError, source.add_listener, 'foo', lambda: None)
        assert str(exc.value) == 'no such event type registered: foo'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('as_coroutine', [True, False], ids=['coroutine', 'normal_function'])
    @pytest.mark.parametrize('event_name, results', [
        ('event_a', [((7, 3), {'x': 6}), ((6, 1), {'a': 5}), ((1, 2), {'a': 3})]),
        ('event_b', [((9, 4), {'c': 1}), ((4, 5), {'b': 4})])
    ], ids=['event_a', 'event_b'])
    def test_fire_event(self, source: EventSourceMixin, event_name, results, as_coroutine):
        """
        Tests that firing an event triggers the right listeners and respects the callback
        priorities.
        """

        def callback(event: Event, *args, **kwargs):
            nonlocal events
            events.append((event, args, kwargs))

        callback = coroutine(callback) if as_coroutine else callback
        events = []
        source.add_listener('event_a', callback, [1, 2], {'a': 3}, priority=CallbackPriority.last)
        source.add_listener('event_b', callback, [4, 5], {'b': 4},
                            priority=CallbackPriority.neutral)
        source.add_listener('event_a', callback, [7, 3], {'x': 6}, priority=CallbackPriority.first)
        source.add_listener('event_a', callback, [6, 1], {'a': 5},
                            priority=CallbackPriority.neutral)
        source.add_listener('event_b', callback, [9, 4], {'c': 1}, priority=CallbackPriority.first)
        yield from source.fire_event(event_name, 'x', 'y', a=1, b=2)

        assert len(events) == len(results)
        for (event, args, kwargs), (expected_args, expected_kwargs) in zip(events, results):
            assert event.source == source
            assert event.name == event_name
            assert event.args == ('x', 'y')
            assert event.kwargs == {'a': 1, 'b': 2}
            assert args == expected_args
            assert kwargs == expected_kwargs

    @pytest.mark.parametrize('event_name', ['event_a', 'foo'],
                             ids=['existing_event', 'nonexistent_event'])
    def test_remove_noexistent_listener(self, source, event_name):
        handle = ListenerHandle(event_name, lambda: None, (), {}, CallbackPriority.neutral)
        exc = pytest.raises(LookupError, source.remove_listener, handle)
        assert str(exc.value) == 'listener not found'

    @pytest.mark.asyncio
    def test_fire_nonexistent_event(self, source):
        with pytest.raises(LookupError) as exc:
            yield from source.fire_event('blah')
        assert str(exc.value) == 'no such event type registered: blah'
