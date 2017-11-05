import gc
from asyncio import Task
from datetime import datetime, timezone, timedelta

import pytest
from async_generator import aclosing

from asphalt.core import Event, Signal, stream_events, wait_event


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
        timestamp = datetime.now(timezone(timedelta(hours=2)))
        event = Event(source, 'sometopic', timestamp.timestamp())
        assert event.utc_timestamp == timestamp
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

    @pytest.mark.asyncio
    async def test_disconnect(self, source):
        """Test that an event listener no longer receives events after it's been removed."""
        events = []
        source.event_a.connect(events.append)
        assert await source.event_a.dispatch(1)

        source.event_a.disconnect(events.append)
        assert await source.event_a.dispatch(2)

        assert len(events) == 1
        assert events[0].args == (1,)

    def test_disconnect_nonexistent_listener(self, source):
        """Test that attempting to remove a nonexistent event listener will not raise an error."""
        source.event_a.connect(lambda event: None)
        source.event_a.disconnect(lambda event: None)

    def test_disconnect_no_listeners(self, source):
        """
        Test that disconnecting a nonexistent listener while listeners is None will still work.

        """
        source.event_a.disconnect(lambda event: None)

    @pytest.mark.asyncio
    async def test_dispatch_event_coroutine(self, source):
        """Test that a coroutine function can be an event listener."""
        async def callback(event: Event):
            events.append(event)

        events = []
        source.event_a.connect(callback)
        assert await source.event_a.dispatch('x', 'y', a=1, b=2)

        assert len(events) == 1
        assert events[0].args == ('x', 'y')
        assert events[0].kwargs == {'a': 1, 'b': 2}

    @pytest.mark.asyncio
    async def test_dispatch_raw(self, source):
        """Test that dispatch_raw() correctly dispatches the given event."""
        events = []
        source.event_a.connect(events.append)
        event = DummyEvent(source, 'event_a', 'x', 'y', a=1, b=2)
        assert await source.event_a.dispatch_raw(event)

        assert events == [event]

    @pytest.mark.asyncio
    async def test_dispatch_log_exceptions(self, event_loop, source, caplog):
        """Test that listener exceptions are logged and that dispatch() resolves to ``False``."""
        def listener(event):
            raise Exception('regular')

        async def coro_listener(event):
            raise Exception('coroutine')

        source.event_a.connect(listener)
        source.event_a.connect(coro_listener)
        assert not await source.event_a.dispatch()

        assert len(caplog.records) == 2
        for record in caplog.records:
            assert 'Uncaught exception in event listener' in record.message

    @pytest.mark.asyncio
    async def test_dispatch_event_no_listeners(self, source):
        """Test that dispatching an event when there are no listeners will still work."""
        assert await source.event_a.dispatch()

    @pytest.mark.asyncio
    async def test_dispatch_event_cancel(self, source):
        """Test that dispatching an event when there are no listeners will still work."""
        source.event_a.connect(lambda event: None)
        future = source.event_a.dispatch()
        future.cancel()
        task = next(t for t in Task.all_tasks() if t is not Task.current_task())
        await task

    @pytest.mark.asyncio
    async def test_connect_twice(self, source):
        """Test that if the same callback is connected twice, the second connect is a no-op."""
        events = []
        source.event_a.connect(events.append)
        source.event_a.connect(events.append)
        assert await source.event_a.dispatch()

        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_dispatch_raw_class_mismatch(self, source):
        """Test that passing an event of the wrong type raises an AssertionError."""
        with pytest.raises(TypeError) as exc:
            await source.event_a.dispatch_raw(Event(source, 'event_a'))

        assert str(exc.value) == 'event must be of type test_event.DummyEvent'

    @pytest.mark.asyncio
    async def test_wait_event(self, source, event_loop):
        event_loop.call_soon(source.event_a.dispatch)
        received_event = await source.event_a.wait_event()
        assert received_event.topic == 'event_a'

    @pytest.mark.parametrize('filter, expected_values', [
        (None, [1, 2, 3]),
        (lambda event: event.args[0] in (3, None), [3])
    ], ids=['nofilter', 'filter'])
    @pytest.mark.asyncio
    async def test_stream_events(self, source, filter, expected_values):
        values = []
        async with aclosing(source.event_a.stream_events(filter)) as stream:
            for i in range(1, 4):
                source.event_a.dispatch(i)

            source.event_a.dispatch(None)

            async for event in stream:
                if event.args[0] is not None:
                    values.append(event.args[0])
                else:
                    break

        assert values == expected_values

    def test_memory_leak(self):
        """
        Test that activating a Signal does not prevent its owner object from being garbage
        collected.

        """
        class SignalOwner:
            dummy = Signal(Event)

        owner = SignalOwner()
        owner.dummy
        del owner
        gc.collect()  # needed on PyPy
        assert next((x for x in gc.get_objects() if isinstance(x, SignalOwner)), None) is None


@pytest.mark.parametrize('filter, expected_value', [
    (None, 1),
    (lambda event: event.args[0] == 3, 3)
], ids=['nofilter', 'filter'])
@pytest.mark.asyncio
async def test_wait_event(event_loop, filter, expected_value):
    """
    Test that wait_event returns the first event matched by the filter, or the first event if there
    is no filter.

    """
    source1 = DummySource()
    for i in range(1, 4):
        event_loop.call_soon(source1.event_a.dispatch, i)

    event = await wait_event([source1.event_a], filter)
    assert event.args == (expected_value,)


@pytest.mark.parametrize('filter, expected_values', [
    (None, [1, 2, 3, 1, 2, 3]),
    (lambda event: event.args[0] in (3, None), [3, 3])
], ids=['nofilter', 'filter'])
@pytest.mark.asyncio
async def test_stream_events(filter, expected_values):
    source1, source2 = DummySource(), DummySource()
    values = []
    async with aclosing(stream_events([source1.event_a, source2.event_b], filter)) as stream:
        for signal in [source1.event_a, source2.event_b]:
            for i in range(1, 4):
                signal.dispatch(i)

        source1.event_a.dispatch(None)

        async for event in stream:
            if event.args[0] is not None:
                values.append(event.args[0])
            else:
                break

    assert values == expected_values
