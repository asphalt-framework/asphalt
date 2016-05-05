Events
======

Events are a handy way to make your code react to changes in another part of the application.
Asphalt events originate from classes that inherit from the
:class:`~asphalt.core.event.EventSource` mixin class.
An example of such class is the :class:`~asphalt.core.context.Context` class.

Listening to events
-------------------

To listen to events dispatched from an :class:`~asphalt.core.event.EventSource`, call
:meth:`~asphalt.core.event.EventSource.add_listener` to add an event listener callback.

The event listener can be either a regular function or a coroutine function::

    from asphalt.core import Event, Context


    def listener(event: Event):
        print(event)


    async def asynclistener(event: Event):
        print(event)


    def event_test():
        context = Context()
        context.add_listener('finished', listener)
        context.add_listener('finished', asynclistener)

        # As we dispatch the "finished" event, the event object is printed twice
        context.dispatch_event('finished', None)

Waiting for a single event
--------------------------

Using the :func:`~asphalt.core.event.wait_event` function you can wait for the next event that
matches one or more topics::

    from asphalt.core import EventSource, wait_event


    async def print_next_event(source: EventSource):
        event = await wait_event(source, 'topic_a')
        print(event)


Receiving events iteratively
----------------------------

With :func:`~asphalt.core.event.stream_events`, you can even asynchronously iterate over incoming
events from an event source::

    from asphalt.core import EventSource, stream_events


    async def listen_to_events(source: EventSource):
        async for event in stream_events(source, ['topic_a', 'topic_b']):
            print(event)


Creating new event sources and event types
------------------------------------------

Any class can be made into an event source simply by inheriting from the
:class:`~asphalt.core.event.EventSource` mixin class. Then you'll just need to register one or
more event topics by using the ``@register_topic`` decorator::

    from asphalt.core import EventSource, Event, register_topic


    @register_topic('topic_1', Event)
    @register_topic('topic_2', Event)
    class MyClass(EventSource):
        ...


The second argument to :func:`~asphalt.core.event.register_topic` is the event class.

When you register topic on your own event source classes, you may also want to create your own
:class:`~asphalt.core.event.Event` subclasses::

    from asphalt.core import Event


    class MyCustomEvent(Event):
        def __init__(source, topic, foo, bar):
            super().__init__(source, topic)
            self.foo = foo
            self.bar = bar

Here, ``foo`` and ``bar`` are properties specific to this event class.

Now you can just pass this class to ``@register_topic`` as the second argument when registering
the topic(s)::

    @register_topic('sometopic', MyCustomEvent)
    class MyEventSource(EventSource):
        pass

And to dispatch a single ``MyCustomEvent`` from your new event source::

    source = MyEventSource()
    source.dispatch_event('sometopic', 'foo_value', bar='bar_value')

