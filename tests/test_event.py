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
        return EventListener(EventSource(), ('foo', 'bar'), lambda: None, (), {})

    def test_repr(self, handle):
        assert repr(handle) == (
            "EventListener(topics=('foo', 'bar'), "
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
        assert handle.topics == ('event_a',)

    def test_add_listener_nonexistent_event(self, source):
        exc = pytest.raises(LookupError, source.add_listener, 'foo', lambda: None)
        assert str(exc.value) == 'no such topic registered: foo'

    @pytest.mark.asyncio
    async def test_dispatch_event_coroutine(self,  source: EventSource):
        """Test that a coroutine function can be an event listener."""
        async def callback(event: Event):
            events.append(event)

        events = []
        source.add_listener('event_a', callback)
        await source.dispatch('event_a', 'x', 'y', a=1, b=2)

        assert len(events) == 1
        assert events[0].args == ('x', 'y')
        assert events[0].kwargs == {'a': 1, 'b': 2}

    @pytest.mark.asyncio
    @pytest.mark.parametrize('construct_first', [True, False], ids=['forward', 'construct'])
    async def dispatch_event_construct(self, source: EventSource, construct_first):
        """
        Test that it doesn't matter if the event was constructed beforehand or if the constructor
        arguments were given directly to dispatch_event().

        """
        events = []
        source.add_listener('event_a', events.append)
        if construct_first:
            event = DummyEvent(source, 'event_a', 'x', 'y', a=1, b=2)
            await source.dispatch(event)
        else:
            await source.dispatch('event_a', 'x', 'y', a=1, b=2)

        assert len(events) == 1
        assert events[0].args == ('x', 'y')
        assert events[0].kwargs == {'a': 1, 'b': 2}

    @pytest.mark.asyncio
    async def dispatch_event_listener_arguments(self, source: EventSource):
        """Test that extra positional and keyword arguments are passed to the listener."""
        arguments = []
        source.add_listener(['event_a'], lambda *args, **kwargs: arguments.append((args, kwargs)),
                            [1, 2], {'x': 6, 'y': 8})
        await source.dispatch('event_a')

        assert len(arguments) == 1
        assert arguments[0][0] == (1, 2)
        assert arguments[0].kwargs == {'x': 1, 'y': 2}

    @pytest.mark.asyncio
    async def dispatch_event_multiple_topic(self, source: EventSource):
        """Test that a one add_listen() call can be made to subscribe to multiple topics."""
        events = []
        source.add_listener(['event_a', 'event_b'], events.append)
        await source.dispatch('event_a', 'x', 'y', a=1, b=2)
        await source.dispatch('event_b', 'c', 'd', g=7, h=8)

        assert len(events) == 2
        assert events[0].args == ('x', 'y')
        assert events[0].kwargs == {'a': 1, 'b': 2}
        assert events[1].args == ('c', 'd')
        assert events[1].kwargs == {'g': 7, 'h': 8}

    @pytest.mark.asyncio
    @pytest.mark.parametrize('from_handle', [True, False],
                             ids=['eventhandle', 'direct'])
    async def test_remove_listener(self, source, from_handle):
        """Test that an event listener no longer receives events after it's been removed."""
        events = []
        handle = source.add_listener('event_a', events.append)
        await source.dispatch('event_a', 1)
        if from_handle:
            handle.remove()
        else:
            source.remove_listener(handle)

        await source.dispatch('event_a', 2)

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
    async def test_dispatch_nonexistent_topic(self, source):
        with pytest.raises(LookupError) as exc:
            await source.dispatch('blah')
        assert str(exc.value) == 'no such topic registered: blah'

    @pytest.mark.asyncio
    async def test_dispatch_pointless_args(self, source):
        """Test that passing variable arguments with an Event instance raises an AssertionError."""
        with pytest.raises(AssertionError) as exc:
            await source.dispatch(DummyEvent(source, 'event_a'), 6)
        assert str(exc.value) == 'passing extra arguments makes no sense here'

    @pytest.mark.asyncio
    async def test_dispatch_event_class_mismatch(self, source):
        """Test that passing an event of the wrong type raises an AssertionError."""
        with pytest.raises(AssertionError) as exc:
            await source.dispatch(Event(source, 'event_a'))
        assert str(exc.value) == 'event class mismatch'
