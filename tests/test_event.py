from asyncio import coroutine
import asyncio
import operator

import pytest

from asphalt.core.event import EventSource, Event, ListenerHandle


class DummyEvent(Event):
    def __init__(self, source, topic, *args, **kwargs):
        super().__init__(source, topic)
        self.args = args
        self.kwargs = kwargs


class TestListenerHandle:
    @pytest.fixture
    def handle(self):
        return ListenerHandle('foo', lambda: None, (), {})

    def test_lt_not_implemented(self, handle):
        exc = pytest.raises(TypeError, operator.lt, handle, 1)
        assert str(exc.value) == 'unorderable types: ListenerHandle() < int()'

    def test_repr(self, handle):
        assert repr(handle) == (
            "ListenerHandle(topic='foo', "
            "callback=test_event.TestListenerHandle.handle.<locals>.<lambda>, args=(), kwargs={})")


class TestEventSource:
    @pytest.fixture
    def source(self):
        event_source = EventSource()
        event_source._register_topics({'event_a': DummyEvent, 'event_b': DummyEvent})
        return event_source

    def test_add_listener(self, source):
        handle = source.add_listener('event_a', lambda: None)
        assert isinstance(handle, ListenerHandle)
        assert handle.topic == 'event_a'

    def test_add_listener_nonexistent_event(self, source):
        exc = pytest.raises(ValueError, source.add_listener, 'foo', lambda: None)
        assert str(exc.value) == 'no such topic registered: foo'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('as_coroutine', [True, False], ids=['coroutine', 'normal'])
    @pytest.mark.parametrize('topic, results', [
        ('event_a', [((1, 2), {'a': 3}), ((7, 3), {'x': 6}), ((6, 1), {'a': 5})]),
        ('event_b', [((4, 5), {'b': 4}), ((9, 4), {'c': 1})])
    ], ids=['event_a', 'event_b'])
    def test_dispatch_event(self, source: EventSource, topic, results, as_coroutine):
        """
        Tests that firing an event triggers the right listeners.
        Also makes sure that callbacks can be either coroutines or normal callables.
        """

        def callback(event: Event, *args, **kwargs):
            nonlocal events
            events.append((event, args, kwargs))
            trigger.set()

        events = []
        trigger = asyncio.Event()
        callback = coroutine(callback) if as_coroutine else callback
        source.add_listener('event_a', callback, [1, 2], {'a': 3})
        source.add_listener('event_b', callback, [4, 5], {'b': 4})
        source.add_listener('event_a', callback, [7, 3], {'x': 6})
        source.add_listener('event_a', callback, [6, 1], {'a': 5})
        source.add_listener('event_b', callback, [9, 4], {'c': 1})
        yield from source.dispatch(topic, 'x', 'y', a=1, b=2)
        yield from trigger.wait()

        assert len(events) == len(results)
        for (event, args, kwargs), (expected_args, expected_kwargs) in zip(events, results):
            assert event.source == source
            assert event.topic == topic
            assert event.args == ('x', 'y')
            assert event.kwargs == {'a': 1, 'b': 2}
            assert args == expected_args
            assert kwargs == expected_kwargs

    @pytest.mark.parametrize('topic', ['event_a', 'foo'],
                             ids=['existing_event', 'nonexistent_event'])
    def test_remove_noexistent_listener(self, source, topic):
        handle = ListenerHandle(topic, lambda: None, (), {})
        exc = pytest.raises(ValueError, source.remove_listener, handle)
        assert str(exc.value) == 'listener not found'

    @pytest.mark.asyncio
    def test_dispatch_nonexistent_topic(self, source):
        with pytest.raises(ValueError) as exc:
            yield from source.dispatch('blah')
        assert str(exc.value) == 'no such topic registered: blah'
