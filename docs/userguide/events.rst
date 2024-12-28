Working with signals and events
===============================

.. py:currentmodule:: asphalt.core

Events are a handy way to make your code react to changes in another part of the
application. To dispatch and listen to events, you first need to have one or more
:class:`Signal` instances as attributes of some class. Each signal needs to be
associated with some :class:`Event` class. Then, when you dispatch a new event
by calling :meth:`Signal.dispatch`, the given event will be passed to all tasks
currently subscribed to that signal.

For listening to events dispatched from a signal, you have two options:

#. :func:`wait_event` (for returning after receiving one event)
#. :func:`stream_events` (for asynchronously iterating over events as they come)

If you only intend to listen to a single signal at once, you can use
:meth:`Signal.wait_event` or :meth:`Signal.stream_events` as shortcuts.

Receiving events iteratively
----------------------------

Here's an example of an event source containing two signals (``somesignal`` and
``customsignal``) and code that subscribes to said signals, dispatches an event on both
signals and then prints them out as they are received::

    from dataclasses import dataclass

    from asphalt.core import Event, Signal, stream_events


    @dataclass
    class CustomEvent(Event):
        extra_argument: str


    class MyEventSource:
        somesignal = Signal(Event)
        customsignal = Signal(CustomEvent)


    async def some_handler():
        source = MyEventSource()
        async with stream_events([source.somesignal, source.customsignal]) as events:
            # Dispatch a basic Event
            source.somesignal.dispatch(Event())

            # Dispatch a CustomEvent
            source.customsignal.dispatch(CustomEvent("extra argument here"))

            async for event in events:
                print(f"received event: {event}")

Waiting for a single event
--------------------------

To wait for the next event dispatched from a given signal, you can use the
:meth:`Signal.wait_event` method::

    async def print_next_event(source: MyEventSource) -> None:
        event = await source.somesignal.wait_event()
        print(event)

You can even wait for the next event dispatched from any of several signals using the
:func:`wait_event` function::

    from asphalt.core import wait_event


    async def print_next_event(
        source1: MyEventSource,
        source2: MyEventSource,
        source3: MyEventSource,
    ) -> None:
        event = await wait_event(
            [source1.some_signal, source2.custom_signal, source3.some_signal]
        )
        print(event)

Filtering received events
-------------------------

You can provide a filter callback that will take an event as the sole argument. Only if
the callback returns ``True``, will the event be received by the listener::

    async def print_next_matching_event(source: MyEventSource) -> None:
        event = await source.customsignal.wait_event(
            lambda event: event.extra_argument == "foo"
        )
        print("Got an event with 'foo' as extra_argument")

The same works for event streams too::

    async def print_matching_events(source: MyEventSource) -> None:
        async with source.customsignal.stream_events(
            lambda event: event.extra_argument == "foo"
        ) as events:
            async for event in events:
                print(event)
