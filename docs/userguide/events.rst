Working with signals and events
===============================

Events are a handy way to make your code react to changes in another part of the application.
To dispatch and listen to events, you first need to have one or more
:class:`~asphalt.core.event.Signal` instances as attributes of some class. Each signal needs to be
associated with some :class:`~asphalt.core.event.Event` class. Then, when you dispatch a new event
by calling :meth:`~asphalt.core.event.Signal.dispatch`, a new instance of this event class will be
constructed and passed to all listener callbacks.

To listen to events dispatched from a signal, you need to have a function or any other callable
that accepts a single positional argument. You then pass this callable to
:meth:`~asphalt.core.event.Signal.connect`. That's it!

To disconnect the callback, simply call :meth:`~asphalt.core.event.Signal.disconnect` with whatever
you passed to :meth:`~asphalt.core.event.Signal.connect` as argument.

Here's how it works::

    from asphalt.core import Event, Signal


    class CustomEvent(Event):
        def __init__(source, topic, extra_argument):
            super().__init__(source, topic)
            self.extra_argument = extra_argument


    class MyEventSource:
        somesignal = Signal(Event)
        customsignal = Signal(CustomEvent)


    def plain_listener(event):
        print('received event: %s' % event)


    async def coro_listener(event):
        print('coroutine listeners are fine too: %s' % event)


    async def some_handler():
        source = MyEventSource()
        source.somesignal.connect(plain_listener)
        source.customsignal.connect(coro_listener)

        # Dispatches an Event instance
        source.somesignal.dispatch()

        # Dispatches a CustomEvent instance (the extra argument is passed to its constructor)
        source.customsignal.dispatch('extra argument here')

Exception handling
------------------

Any exceptions raised by the listener callbacks are logged to the ``asphalt.core.event`` logger.
Additionally, the future returned by :meth:`~asphalt.core.event.Signal.dispatch` resolves to
``True`` if no exceptions were raised during the processing of listeners. This was meant as a
convenience for use with tests where you can just do
``assert await thing.some_signal.dispatch('foo')``.

Waiting for a single event
--------------------------

To wait for the next event dispatched from a given signal, you can use the
:meth:`~asphalt.core.event.Signal.wait_event` method::

    async def print_next_event(source):
        event = await source.somesignal.wait_event()
        print(event)

You can even wait for the next event dispatched from any of several signals using the
:func:`~asphalt.core.event.wait_event` function::

    from asphalt.core import wait_event


    async def print_next_event(source1, source2, source3):
        event = await wait_event(source1.some_signal, source2.another_signal, source3.some_signal)
        print(event)

As a convenience, you can provide a filter callback that will cause the call to only return when
the callback returns ``True``::

    async def print_next_matching_event(source1, source2, source3):
        event = await wait_event(source1.some_signal, source2.another_signal, source3.some_signal,
                                 lambda event: event.myrandomproperty == 'foo')
        print(event)

Receiving events iteratively
----------------------------

With :meth:`~asphalt.core.event.Signal.stream_events`, you can even asynchronously iterate over
events dispatched from a signal::

    from contextlib import aclosing  # on Python < 3.10, import from async_generator or contextlib2


    async def listen_to_events(source):
        async with aclosing(source.somesignal.stream_events()) as stream:
            async for event in stream:
                print(event)

Using :func:`~asphalt.core.event.stream_events`, you can stream events from multiple signals::

    from asphalt.core import stream_events


    async def listen_to_events(source1, source2, source3):
        stream = stream_events(source1.some_signal, source2.another_signal, source3.some_signal)
        async with aclosing(stream):
            async for event in stream:
                print(event)

The filtering capability of :func:`~asphalt.core.event.wait_event` works here too::

    async def listen_to_events(source1, source2, source3):
        stream = stream_events(source1.some_signal, source2.another_signal, source3.some_signal,
                               lambda event: event.randomproperty == 'foo')
        async with aclosing(stream):
            async for event in stream:
                print(event)
