import asyncio
from asyncio.futures import Future

import pytest

from asphalt.core.event import (
    EventSource, Event, EventListener, EventDispatchError, stream_events, register_topic,
    wait_event)


class DummyEvent(Event):
    def __init__(self, source, topic, *args, **kwargs):
        super().__init__(source, topic)
        self.args = args
        self.kwargs = kwargs


@register_topic('event_a', DummyEvent)
@register_topic('event_b', DummyEvent)
class DummySource(EventSource):
    pass


@pytest.fixture
def source():
    return DummySource()


def test_event_repr(source):
    event = Event(source, 'sometopic')
    assert repr(event) == "Event(source=%r, topic='sometopic')" % source


class TestEventListener:
    @pytest.fixture
    def handle(self):
        return EventListener(EventSource(), ('foo', 'bar'), lambda: None, (), {})

    def test_repr(self, handle):
        assert repr(handle) == (
            "EventListener(topics=('foo', 'bar'), "
            "callback=test_event.TestEventListener.handle.<locals>.<lambda>, args=(), kwargs={})")


class TestRegisterTopic:
    def test_incompatible_class(self):
        """
        Test that attempting to use the @register_topic decorator on an incompatible class raises a
        TypeError.

        """
        target_class = type('TestType', (object,), {})
        exc = pytest.raises(TypeError, register_topic('some_event', DummyEvent), target_class)
        assert str(exc.value) == 'cls must be a subclass of EventSource'

    def test_incompatible_override(self):
        """
        Test that attempting to override an event topic with an incompatible Event subclass raises
        a TypeError.

        """
        target_class = type('TestType', (EventSource,), {})
        register_topic('some_event', DummyEvent)(target_class)
        exc = pytest.raises(TypeError, register_topic('some_event', Event), target_class)
        assert str(exc.value) == ('cannot override event class for topic "some_event" -- event '
                                  'class asphalt.core.event.Event is not a subclass of '
                                  'test_event.DummyEvent')

    @pytest.mark.asyncio
    async def test_topic_override(self):
        """
        Test that re-registering an event topic with a subclass of the original event class is
        allowed.

        """
        target_class = type('TestType', (EventSource,), {})
        event_subclass = type('EventSubclass', (DummyEvent,), {})
        register_topic('some_event', DummyEvent)(target_class)
        register_topic('some_event', event_subclass)(target_class)
        events = []
        source = target_class()
        source.add_listener('some_event', events.append)
        await source.dispatch_event('some_event', return_future=True)
        assert isinstance(events[0], event_subclass)


class TestEventSource:
    def test_add_listener(self, source):
        handle = source.add_listener('event_a', lambda: None)
        assert isinstance(handle, EventListener)
        assert handle.topics == ('event_a',)

    def test_add_listener_nonexistent_event(self, source):
        exc = pytest.raises(LookupError, source.add_listener, 'foo', lambda: None)
        assert str(exc.value) == 'no such topic registered: foo'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('from_handle', [True, False], ids=['eventhandle', 'direct'])
    async def test_remove_listener(self, source, from_handle):
        """Test that an event listener no longer receives events after it's been removed."""
        events = []
        handle = source.add_listener('event_a', events.append)
        await source.dispatch_event('event_a', 1, return_future=True)

        if from_handle:
            handle.remove()
        else:
            source.remove_listener(handle)

        await source.dispatch_event('event_a', 2, return_future=True)

        assert len(events) == 1
        assert events[0].args == (1,)

    @pytest.mark.parametrize('topic', ['event_a', 'foo'],
                             ids=['existing_event', 'nonexistent_event'])
    def test_remove_nonexistent_listener(self, source, topic):
        """Test that attempting to remove a nonexistent event listener raises a LookupError."""
        handle = EventListener(source, topic, lambda: None, (), {})
        exc = pytest.raises(LookupError, source.remove_listener, handle)
        assert str(exc.value) == 'listener not found'

    @pytest.mark.asyncio
    async def test_dispatch_event_coroutine(self, source: EventSource):
        """Test that a coroutine function can be an event listener."""
        async def callback(event: Event):
            events.append(event)

        events = []
        source.add_listener('event_a', callback)
        await source.dispatch_event('event_a', 'x', 'y', return_future=True, a=1, b=2)

        assert len(events) == 1
        assert events[0].args == ('x', 'y')
        assert events[0].kwargs == {'a': 1, 'b': 2}

    @pytest.mark.asyncio
    @pytest.mark.parametrize('construct_first', [True, False], ids=['forward', 'construct'])
    async def test_dispatch_event_construct(self, source: EventSource, construct_first):
        """
        Test that it doesn't matter if the event was constructed beforehand or if the constructor
        arguments were given directly to dispatch_event().

        """
        events = []
        source.add_listener('event_a', events.append)
        if construct_first:
            event = DummyEvent(source, 'event_a', 'x', 'y', a=1, b=2)
            await source.dispatch_event(event, return_future=True)
        else:
            await source.dispatch_event('event_a', 'x', 'y', return_future=True, a=1, b=2)

        assert len(events) == 1
        assert events[0].args == ('x', 'y')
        assert events[0].kwargs == {'a': 1, 'b': 2}

    @pytest.mark.asyncio
    async def test_dispatch_event_listener_arguments(self, source: EventSource):
        """Test that extra positional and keyword arguments are passed to the listener."""
        arguments = []
        source.add_listener(['event_a'],
                            lambda event, *args, **kwargs: arguments.append((args, kwargs)),
                            [1, 2], {'x': 6, 'y': 8})
        await source.dispatch_event('event_a', return_future=True)

        assert len(arguments) == 1
        assert arguments[0][0] == (1, 2)
        assert arguments[0][1] == {'x': 6, 'y': 8}

    @pytest.mark.asyncio
    @pytest.mark.parametrize('topics', [
        ['event_a', 'event_b'],
        'event_a, event_b'
    ], ids=['list', 'comma-separated'])
    async def test_dispatch_event_multiple_topic(self, source: EventSource, topics):
        """Test that a one add_listen() call can be made to subscribe to multiple topics."""
        events = []
        source.add_listener(topics, events.append)
        await source.dispatch_event('event_a', 'x', 'y', return_future=True, a=1, b=2)
        await source.dispatch_event('event_b', 'c', 'd', return_future=True, g=7, h=8)

        assert len(events) == 2
        assert events[0].args == ('x', 'y')
        assert events[0].kwargs == {'a': 1, 'b': 2}
        assert events[1].args == ('c', 'd')
        assert events[1].kwargs == {'g': 7, 'h': 8}

    @pytest.mark.asyncio
    async def test_dispatch_event_listener_exception_logging(self, event_loop, source, caplog):
        """Test that listener exceptions are logged when return_future is False."""
        def listener(event):
            raise Exception('regular')

        async def coro_listener(event):
            raise Exception('coroutine')

        future = Future()
        source.add_listener('event_a', listener)
        source.add_listener('event_a', coro_listener)
        source.add_listener('event_a', future.set_result)
        source.dispatch_event('event_a')
        await future

        assert len(caplog.records) == 2
        for record in caplog.records:
            assert 'uncaught exception in event listener' in record.message

    def test_dispatch_event_no_listeners(self, source):
        """Test that None is returned when no listeners are present and return_future is False."""
        assert source.dispatch_event('event_a') is None

    @pytest.mark.asyncio
    async def test_dispatch_event_listener_exceptions(self, source):
        """
        Test that multiple exceptions raised by listeners are combined into one EventDispatchError.

        """
        def plain_error(event):
            raise plain_exception

        async def async_error(event):
            raise async_exception

        plain_exception = ValueError('foo')
        async_exception = KeyError('bar')
        plain_listener = source.add_listener('event_a', plain_error)
        async_listener = source.add_listener('event_a', async_error)
        event = DummyEvent(source, 'event_a')
        with pytest.raises(EventDispatchError) as exc:
            await source.dispatch_event(event, return_future=True)

        assert exc.value.event is event
        assert exc.value.exceptions == [
            (plain_listener, plain_exception),
            (async_listener, async_exception)
        ]
        assert 'plain_error' in str(exc.value)
        assert 'async_error' in str(exc.value)
        assert '-------------------------------\n' in str(exc.value)

    @pytest.mark.asyncio
    async def test_dispatch_event_nonexistent_topic(self, source):
        with pytest.raises(LookupError) as exc:
            await source.dispatch_event('blah')
        assert str(exc.value) == 'no such topic registered: blah'

    @pytest.mark.asyncio
    async def test_dispatch_event_pointless_args(self, source):
        """Test that passing variable arguments with an Event instance raises an AssertionError."""
        with pytest.raises(AssertionError) as exc:
            source.dispatch_event(DummyEvent(source, 'event_a'), 6)
        assert str(exc.value) == 'passing extra arguments makes no sense here'

    @pytest.mark.asyncio
    async def test_dispatch_event_class_mismatch(self, source):
        """Test that passing an event of the wrong type raises an AssertionError."""
        with pytest.raises(AssertionError) as exc:
            await source.dispatch_event(Event(source, 'event_a'))
        assert str(exc.value) == 'event class mismatch'


@pytest.mark.asyncio
async def test_wait_event(source, event_loop):
    event = DummyEvent(source, 'event_a')
    event_loop.call_soon(source.dispatch_event, event)
    received_event = await wait_event(source, 'event_a')
    assert received_event is event


@pytest.mark.asyncio
async def test_stream_events(source, event_loop):
    async def generate_events():
        await asyncio.sleep(0.1)
        source.dispatch_event('event_a', index=1)
        await asyncio.sleep(0.1)
        source.dispatch_event('event_a', index=2)
        await asyncio.sleep(0.1)
        source.dispatch_event('event_a', index=3)

    event_loop.create_task(generate_events())
    last_number = 0
    async for event in stream_events(source, 'event_a'):
        assert event.kwargs['index'] == last_number + 1
        last_number += 1
        if last_number == 3:
            break
