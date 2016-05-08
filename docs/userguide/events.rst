Events
======

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

By default, all exceptions raised by listener callbacks are just sent to the logger
(``asphalt.core.event``). If the dispatcher needs to know about any exceptions raised by listeners,
it can call :meth:`~asphalt.core.event.Signal.dispatch` with ``return_future=True``. This will
cause a :class:`~asyncio.Future` to be returned and, when awaited, will raise a
:class:`~asphalt.core.event.EventDispatchError` if any listener raised an exception. This exception
will contain every exception that was raised, along with the information regarding which callback
raised which exception.

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

Receiving events iteratively
----------------------------

With :meth:`~asphalt.core.event.Signal.stream_events`, you can even asynchronously iterate over
events dispatched from a signal::

    async def listen_to_events(source):
        async for event in source.somesignal.stream_events():
            print(event)

Using :func:`~asphalt.core.event.stream_events`, you can stream events from multiple signals::

    from asphalt.core import stream_events


    async def listen_to_events(source1, source2, source3):
        async for event in stream_events(source1.some_signal, source2.another_signal,
                                         source3.some_signal):
            print(event)

