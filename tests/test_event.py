import gc
from datetime import datetime, timedelta, timezone

import pytest
from anyio import create_task_group, fail_after
from anyio.abc import TaskStatus
from anyio.lowlevel import checkpoint

from asphalt.core import Event, Signal, SignalQueueFull, stream_events, wait_event

pytestmark = pytest.mark.anyio()


class DummyEvent(Event):
    def __init__(self, *args, **kwargs):
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
        event = Event()
        event.time = timestamp.timestamp()
        assert event.utc_timestamp == timestamp
        assert event.utc_timestamp.tzinfo == timezone.utc

    def test_event_repr(self, source):
        event = Event()
        event.source = source
        event.topic = "sometopic"
        assert repr(event) == f"Event(source={source!r}, topic='sometopic')"


class TestSignal:
    def test_class_attribute_access(self):
        """
        Test that accessing the descriptor on the class level returns the same signal
        instance.

        """
        signal = Signal(DummyEvent)

        class EventSource:
            dummysignal = signal

        assert EventSource.dummysignal is signal

    async def test_dispatch_event_type_mismatch(self, source):
        """Test that trying to dispatch an event of the wrong type raises TypeError."""
        pattern = (
            f"Event type mismatch: event \\(str\\) is not a subclass of "
            f"{__name__}.DummyEvent"
        )
        with pytest.raises(TypeError, match=pattern):
            source.event_a.dispatch("foo")

    async def test_dispatch_event_no_listeners(self, source):
        """
        Test that dispatching an event when there are no listeners will still work.

        """
        source.event_a.dispatch(DummyEvent())

    async def test_dispatch_event_buffer_overflow(self, source):
        """
        Test that dispatching to a subscriber that has a full queue raises the
        SignalQueueFull warning.

        """
        received_events = []

        async def receive_events(task_status: TaskStatus[None]) -> None:
            async with source.event_a.stream_events(max_queue_size=1) as stream:
                task_status.started()
                async for event in stream:
                    received_events.append(event)

        async with create_task_group() as tg:
            await tg.start(receive_events)
            source.event_a.dispatch(DummyEvent(1))
            with pytest.warns(SignalQueueFull):
                source.event_a.dispatch(DummyEvent(2))
                source.event_a.dispatch(DummyEvent(3))

            # Give the task a chance to run, then cancel
            await checkpoint()
            tg.cancel_scope.cancel()

        assert len(received_events) == 1

    @pytest.mark.parametrize(
        "filter, expected_value",
        [
            pytest.param(None, 1, id="nofilter"),
            pytest.param(lambda event: event.args[0] == 3, 3, id="filter"),
        ],
    )
    async def test_wait_event(self, source, filter, expected_value):
        async def dispatch_events() -> None:
            for i in range(1, 4):
                source.event_a.dispatch(DummyEvent(i))

        async with create_task_group() as tg:
            tg.start_soon(dispatch_events)
            with fail_after(1):
                event = await wait_event([source.event_a], filter)

        assert event.args == (expected_value,)

    @pytest.mark.parametrize(
        "filter, expected_values",
        [
            pytest.param(None, [1, 2, 3], id="nofilter"),
            pytest.param(lambda event: event.args[0] in (3, None), [3], id="filter"),
        ],
    )
    async def test_stream_events(self, source, filter, expected_values):
        values = []
        async with source.event_a.stream_events(filter) as stream:
            for i in range(1, 4):
                source.event_a.dispatch(DummyEvent(i))

            async for event in stream:
                values.append(event.args[0])
                if event.args[0] == 3:
                    break

        assert values == expected_values

    def test_memory_leak(self):
        """
        Test that activating a Signal does not prevent its owner object from being
        garbage collected.

        """

        class SignalOwner:
            dummy = Signal(Event)

        owner = SignalOwner()
        owner.dummy
        del owner
        gc.collect()  # needed on PyPy
        assert (
            next((x for x in gc.get_objects() if isinstance(x, SignalOwner)), None)
            is None
        )


@pytest.mark.parametrize(
    "filter, expected_value",
    [
        pytest.param(None, 1, id="nofilter"),
        pytest.param(lambda event: event.args[0] == 3, 3, id="filter"),
    ],
)
async def test_wait_event(source, filter, expected_value):
    """
    Test that wait_event returns the first event matched by the filter, or the first
    event if there is no filter.

    """

    async def dispatch_events() -> None:
        for i in range(1, 4):
            source.event_a.dispatch(DummyEvent(i))

    async with create_task_group() as tg:
        tg.start_soon(dispatch_events)
        with fail_after(1):
            event = await wait_event([source.event_a], filter)

    assert event.args == (expected_value,)


@pytest.mark.parametrize(
    "filter, expected_values",
    [
        pytest.param(None, [1, 2, 3, 1, 2, 3], id="nofilter"),
        pytest.param(lambda event: event.args[0] in (3, None), [3, 3], id="filter"),
    ],
)
async def test_stream_events(filter, expected_values):
    source1, source2 = DummySource(), DummySource()
    values = []
    async with stream_events([source1.event_a, source2.event_b], filter) as stream:
        for signal in [source1.event_a, source2.event_b]:
            for i in range(1, 4):
                signal.dispatch(DummyEvent(i))

        signal.dispatch(DummyEvent(None))

        async for event in stream:
            if event.args[0] is None:
                break

            values.append(event.args[0])

    assert values == expected_values
