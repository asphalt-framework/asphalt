import asyncio
from asyncio.futures import Future
from datetime import datetime, timezone

import pytest

from asphalt.core.event import Event, EventDispatchError, Signal, stream_events, wait_event


class DummyEvent(Event):
    def __init__(self, source, topic, *args, **kwargs):
        super().__init__(source, topic)
        self.args = args
        self.kwargs = kwargs


class DummySource:
    event_a = Signal(DummyEvent)
    event_b = Signal(DummyEvent)


@pytest.fixture
def source():
    return DummySource()


class TestEvent:
    def test_utc_timestamp(self, source):
        event = Event(source, 'sometopic')
        assert isinstance(event.utc_timestamp, datetime)
        assert event.utc_timestamp.tzinfo == timezone.utc

    def test_event_repr(self, source):
        event = Event(source, 'sometopic')
        assert repr(event) == "Event(source=%r, topic='sometopic')" % source


class TestSignal:
    def test_class_attribute_access(self):
        """
        Test that accessing the descriptor on the class level returns the same signal instance.

        """
        signal = Signal(DummyEvent)

        class EventSource:
            dummysignal = signal

        assert EventSource.dummysignal is signal

    def test_multi_attribute_assignment(self):
        """
        Test that assigning the signal object to multiple class attributes raises a ``LookupError``
        on instance attribute access.

        """
        signal = Signal(DummyEvent)

        class EventSource:
            dummysignal = signal
            dummysignal2 = signal

        with pytest.raises(LookupError) as exc:
            getattr(EventSource(), 'dummysignal')

        assert str(exc.value) == \
            'this Signal was assigned to multiple attributes: dummysignal, dummysignal2'

    @pytest.mark.asyncio
    async def test_disconnect(self, source):
        """Test that an event listener no longer receives events after it's been removed."""
        events = []
        source.event_a.connect(events.append)
        await source.event_a.dispatch(1, return_future=True)

        source.event_a.disconnect(events.append)
        await source.event_a.dispatch(2, return_future=True)

        assert len(events) == 1
        assert events[0].args == (1,)

    def test_remove_nonexistent_listener(self, source):
        """Test that attempting to remove a nonexistent event listener will not raise an error."""
        source.event_a.disconnect(lambda: None)

    @pytest.mark.asyncio
    async def test_dispatch_event_coroutine(self, source):
        """Test that a coroutine function can be an event listener."""
        async def callback(event: Event):
            events.append(event)

        events = []
        source.event_a.connect(callback)
        await source.event_a.dispatch('x', 'y', return_future=True, a=1, b=2)

        assert len(events) == 1
        assert events[0].args == ('x', 'y')
        assert events[0].kwargs == {'a': 1, 'b': 2}

    @pytest.mark.asyncio
    async def test_dispatch_event(self, source):
        """Test that dispatch_event() correctly dispatches the given event."""
        events = []
        source.event_a.connect(events.append)
        event = DummyEvent(source, 'event_a', 'x', 'y', a=1, b=2)
        await source.event_a.dispatch_event(event, return_future=True)

        assert events == [event]

    @pytest.mark.asyncio
    async def test_dispatch_listener_exception_logging(self, event_loop, source, caplog):
        """Test that listener exceptions are logged when return_future is False."""
        def listener(event):
            try:
                raise Exception('regular')
            finally:
                future1.set_result(None)

        async def coro_listener(event):
            try:
                raise Exception('coroutine')
            finally:
                future2.set_result(None)

        future1, future2 = Future(), Future()
        source.event_a.connect(listener)
        source.event_a.connect(coro_listener)
        source.event_a.dispatch()
        await asyncio.gather(future1, future2)

        assert len(caplog.records) == 2
        for record in caplog.records:
            assert 'uncaught exception in event listener' in record.message

    def test_dispatch_event_no_listeners(self, source):
        """Test that None is returned when no listeners are present and return_future is False."""
        assert source.event_a.dispatch() is None

    @pytest.mark.asyncio
    async def test_dispatch_event_listener_exceptions(self, source):
        """
        Test that multiple exceptions raised by listeners are combined into one EventDispatchError.

        """
        def listener(event):
            raise plain_exception

        async def coro_listener(event):
            raise async_exception

        plain_exception = ValueError('foo')
        async_exception = KeyError('bar')
        plain_listener = source.event_a.connect(listener)
        async_listener = source.event_a.connect(coro_listener)
        event = DummyEvent(source, 'event_a')
        with pytest.raises(EventDispatchError) as exc:
            await source.event_a.dispatch_event(event, return_future=True)

        assert exc.value.event is event
        assert exc.value.exceptions == [
            (plain_listener, plain_exception),
            (async_listener, async_exception)
        ]
        assert 'listener' in str(exc.value)
        assert 'coro_listener' in str(exc.value)
        assert '-------------------------------\n' in str(exc.value)

    @pytest.mark.asyncio
    async def test_dispatch_event_class_mismatch(self, source):
        """Test that passing an event of the wrong type raises an AssertionError."""
        with pytest.raises(AssertionError) as exc:
            await source.event_a.dispatch_event(Event(source, 'event_a'))
        assert str(exc.value) == 'event must be of type test_event.DummyEvent'

    @pytest.mark.asyncio
    async def test_wait_event(self, source, event_loop):
        task = event_loop.create_task(source.event_a.wait_event())
        event_loop.call_soon(source.event_a.dispatch)
        received_event = await task
        assert received_event.topic == 'event_a'

    @pytest.mark.asyncio
    async def test_stream_events(self, source, event_loop):
        async def generate_events():
            await asyncio.sleep(0.1)
            source.event_a.dispatch(1)
            await asyncio.sleep(0.1)
            source.event_a.dispatch(2)
            await asyncio.sleep(0.1)
            source.event_a.dispatch(3)

        event_loop.create_task(generate_events())
        last_number = 0
        async for event in source.event_a.stream_events():
            assert event.args[0] == last_number + 1
            last_number += 1
            if last_number == 3:
                break


@pytest.mark.asyncio
async def test_wait_event(event_loop):
    """Test that wait_event catches events coming from any of the given signals."""
    source1, source2 = DummySource(), DummySource()

    for signal in source1.event_a, source2.event_b:
        task = event_loop.create_task(wait_event(signal))
        event_loop.call_soon(signal.dispatch)
        received_event = await task
        assert received_event.topic == signal.topic


@pytest.mark.asyncio
async def test_stream_events(event_loop):
    async def generate_events():
        await asyncio.sleep(0.1)
        source1.event_a.dispatch(1)
        await asyncio.sleep(0.1)
        source2.event_b.dispatch(2)
        await asyncio.sleep(0.1)
        source1.event_a.dispatch(3)

    source1, source2 = DummySource(), DummySource()
    event_loop.create_task(generate_events())
    last_number = 0
    async for event in stream_events(source1.event_a, source2.event_b):
        assert event.args[0] == last_number + 1
        last_number += 1
        if last_number == 3:
            break
