Working with signals and events
===============================

.. py:currentmodule:: asphalt.core

Events are a handy way to make your code react to changes in another part of the
application. To dispatch and listen to events, you first need to have one or more
:class:`Signal` instances as attributes of some class. Each signal needs to be
associated with some :class:`Event` class. Then, when you dispatch a new event
by calling :meth:`Signal.dispatch`, a new instance of this event class will be
constructed and passed to all listener callbacks.

To listen to events dispatched from a signal, you need to have a function or any other
callable that accepts a single positional argument. You then pass this callable to
:meth:`Signal.connect`. That's it!

To disconnect the callback, simply call :meth:`Signal.disconnect` with whatever
you passed to :meth:`Signal.connect` as argument.

Here's how it works::

    from asphalt.core import Event, Signal


    class CustomEvent(Event):
        def __init__(self, source, topic, extra_argument):
            super().__init__(source, topic)
            self.extra_argument = extra_argument


    class MyEventSource:
        somesignal = Signal(Event)
        customsignal = Signal(CustomEvent)


    def plain_listener(event):
        print(f'received event: {event}')


    async def coro_listener(event):
        print(f'coroutine listeners are fine too: {event}')


    async def some_handler():
        source = MyEventSource()
        source.somesignal.connect(plain_listener)
        source.customsignal.connect(coro_listener)

        # Dispatches an Event instance
        source.somesignal.dispatch()

        # Dispatches a CustomEvent instance (the extra argument is passed to its
        # constructor)
        source.customsignal.dispatch('extra argument here')

Exception handling
------------------

Any exceptions raised by the listener callbacks are logged to the ``asphalt.core.event``
logger. Additionally, the future returned by :meth:`Signal.dispatch` resolves to
``True`` if no exceptions were raised during the processing of listeners. This was meant
as a convenience for use with tests where you can just do
``assert await thing.some_signal.dispatch('foo')``.

Waiting for a single event
--------------------------

To wait for the next event dispatched from a given signal, you can use the
:meth:`Signal.wait_event` method::

    async def print_next_event(source):
        event = await source.somesignal.wait_event()
        print(event)

You can even wait for the next event dispatched from any of several signals using the
:func:`wait_event` function::

    from asphalt.core import wait_event


    async def print_next_event(source1, source2, source3):
        event = await wait_event(
            [source1.some_signal, source2.another_signal, source3.some_signal]
        )
        print(event)

As a convenience, you can provide a filter callback that will cause the call to only return when
the callback returns ``True``::

    async def print_next_matching_event(source1, source2, source3):
        event = await wait_event(
            [source1.some_signal, source2.another_signal, source3.some_signal],
            lambda event: event.myrandomproperty == 'foo'
        )
        print(event)

Receiving events iteratively
----------------------------

With :meth:`Signal.stream_events`, you can even asynchronously iterate over
events dispatched from a signal::

    async def listen_to_events(source):
        async with source.somesignal.stream_events() as stream:
            async for event in stream:
                print(event)

Using :func:`stream_events`, you can stream events from multiple signals::

    from asphalt.core import stream_events


    async def listen_to_events(source1, source2, source3):
        async with stream_events(
            [source1.some_signal, source2.another_signal, source3.some_signal]
        ) as stream:
            async for event in stream:
                print(event)

The filtering capability of :func:`wait_event` works here too::

    async def listen_to_events(source1, source2, source3):
        async with stream_events(
            [source1.some_signal, source2.another_signal, source3.some_signal],
            lambda event: event.randomproperty == 'foo'
        ) as stream:
            async for event in stream:
                print(event)
