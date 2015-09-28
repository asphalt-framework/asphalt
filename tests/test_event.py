from asyncio import coroutine
import asyncio

import pytest

from asphalt.core.event import EventSource, Event, EventListener


class DummyEvent(Event):
    def __init__(self, source, topic, *args, **kwargs):
        super().__init__(source, topic)
        self.args = args
        self.kwargs = kwargs


class TestEventListener:
    @pytest.fixture
    def handle(self):
        return EventListener(EventSource(), 'foo', lambda: None, (), {})

    def test_repr(self, handle):
        assert repr(handle) == (
            "EventListener(topic='foo', "
            "callback=test_event.TestEventListener.handle.<locals>.<lambda>, args=(), kwargs={})")


class TestEventSource:
    @pytest.fixture
    def source(self):
        event_source = EventSource()
        event_source._register_topics({'event_a': DummyEvent, 'event_b': DummyEvent})
        return event_source

    def test_add_listener(self, source):
        handle = source.add_listener('event_a', lambda: None)
        assert isinstance(handle, EventListener)
        assert handle.topic == 'event_a'

    def test_add_listener_nonexistent_event(self, source):
        exc = pytest.raises(LookupError, source.add_listener, 'foo', lambda: None)
        assert str(exc.value) == 'no such topic registered: foo'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('as_coroutine', [True, False], ids=['coroutine', 'normal'])
    @pytest.mark.parametrize('construct_first', [True, False], ids=['forward', 'construct'])
    @pytest.mark.parametrize('topic, results', [
        ('event_a', [((1, 2), {'a': 3}), ((7, 3), {'x': 6}), ((6, 1), {'a': 5})]),
        ('event_b', [((4, 5), {'b': 4}), ((9, 4), {'c': 1})])
    ], ids=['event_a', 'event_b'])
    def test_dispatch_event(self, source: EventSource, topic, results, as_coroutine,
                            construct_first):
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
        if construct_first:
            event = DummyEvent(source, topic, 'x', 'y', a=1, b=2)
            yield from source.dispatch(event)
        else:
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
        handle = EventListener(source, topic, lambda: None, (), {})
        exc = pytest.raises(LookupError, source.remove_listener, handle)
        assert str(exc.value) == 'listener not found'

    @pytest.mark.asyncio
    def test_dispatch_nonexistent_topic(self, source):
        with pytest.raises(LookupError) as exc:
            yield from source.dispatch('blah')
        assert str(exc.value) == 'no such topic registered: blah'
