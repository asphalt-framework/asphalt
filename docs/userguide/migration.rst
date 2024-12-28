Migrating from Asphalt 4.x to 5.x
=================================

.. py:currentmodule:: asphalt.core

Resources
---------

#. Adding resources should now be done with the :func:`add_resource` function rather than
   the :meth:`Context.add_resource` method
#. ``require_resource()`` is now :func:`get_resource_nowait`
#. ``get_resource()`` is now :func:`get_resource_nowait` with ``optional=True``
#. ``request_resource()`` is now :func:`get_resource`

Component classes
-----------------

#. The ``ctx`` parameter was removed from :meth:`Component.start`
#. The functions for adding and getting resources have changed (see above)

Before::

    from asphalt.core import Component, Context, add_resource, request_resource

    class MyComponent(Component):
        async def start(self, ctx: Context) -> None:
            resource = await ctx.request_resource(int, "integer_resource")
            ctx.add_resource("simple-string")

After::

    from asphalt.core import Component, add_resource, get_resource

    class MyComponent(Component):
        async def start(self) -> None:
            resource = await get_resource(int, "integer_resource")
            add_resource("simple-string")

Container components
--------------------

#. The ``ContainerComponent`` class has been removed in favor of allowing any
   :class:`Component` subclass to have subcomponents. As such, they no longer take a
   ``components`` argument in their ``__init__()`` methods, so you're free to make them
   data classes if you like.
#. There is no longer any need to call ``super().start()`` in the
   :meth:`~Component.start` method
#. Any :meth:`~Component.add_component` calls must be made in the initializer instead of
   the :meth:`~Component.start` method

Before::

    from asphalt.core import ContainerComponent, Context

    class MyContainer(ContainerComponent):
        def __init__(self, components):
            super().__init__(components)

        async def start(self, ctx: Context) -> None:
            await super().start(ctx)
            self.add_component("another", AnotherComponent)
            ...

After::

    from asphalt.core import Component

    class MyContainer(Component):
        def __init__(self) -> None:
            self.add_component("another", AnotherComponent)

        async def start(self) -> None:
           ...

CLI application components
--------------------------

The ``ctx`` parameter has been removed from the :meth:`CLIApplicationComponent.run`
method.

Before::

    from asphalt.core import CLIApplicationComponent

    class MyApp(CLIApplicationComponent):
        def __init__(self, components):
            super().__init__(components)

        async def start(self, ctx: Context) -> None:
            self.add_component("another", AnotherComponent)
            ...

        async def run(self, ctx: Context) -> None:
            ...

After::

    from asphalt.core import CLIApplicationComponent

    class MyApp(CLIApplicationComponent):
        def __init__(self) -> None:
            self.add_component("another", AnotherComponent)

        async def start(self) -> None:
           ...

        async def run(self) -> None:
            ...

Starting tasks at component startup
-----------------------------------

As Asphalt is now built on top of AnyIO_, tasks should be started and torn down in
compliance with `structured concurrency`_, and using AnyIO's task APIs. In practice,
if you're starting tasks in :meth:`Component.start`, you should probably use the
:func:`start_service_task` function.

.. note:: Note that the task spawning functions take callables, not coroutine objects,
    so drop the ``()``. If you need to pass keyword arguments, use either a lambda or
    :func:`functools.partial` to do so.

Before::

    from asyncio import CancelledError, create_task
    from contextlib import suppress

    from asphalt.core import Component, Context
    from asphalt.core.context import context_teardown

    class MyComponent(Component):
        @context_teardown
        async def start(self, ctx: Context) -> None:
            task = create_task(self.sometaskfunc(1, kwarg="foo"))
            yield
            task.cancel()
            with suppress(CancelledError):
                await task

        async def sometaskfunc(self, arg, *, kwarg) -> None:
            ...

After::

    from functools import partial

    from asphalt.core import Component, start_service_task

    class MyComponent(Component):
        async def start(self) -> None:
            await start_service_task(partial(self.sometaskfunc, 1, kwarg="foo"), "sometask")

        async def sometaskfunc(self, arg, *, kwarg) -> None:
            ...

.. seealso:: :doc:`concurrency`

Starting ad-hoc tasks after application startup
-----------------------------------------------

Starting tasks that complete by themselves within the run time of the application is now
done using **task factories**. Task factories start their tasks in the same AnyIO task
group, and you can pass settings common to all the spawned tasks to
:func:`start_background_task_factory`.

Before::

    from asyncio import create_task

    async def my_function() -> None:
        task = create_task(sometaskfunc(1, kwarg="foo"))

    async def sometaskfunc(arg, *, kwarg) -> None:
        ...

After::

    from functools import partial

    from asphalt.core import Component, add_resource, start_background_task_factory

    class MyServiceComponent(Component):
        async def start(self) -> None:
            factory = await start_background_task_factory()
            add_resource(factory)

    # And then in another module:
    from asphalt.core import TaskFactory, get_resource_nowait

    async def my_function() -> None:
        factory = get_resource_nowait(TaskFactory)
        task = factory.start_task_soon(partial(sometaskfunc, 1, kwarg="foo"))

    async def sometaskfunc(arg, *, kwarg) -> None:
        ...

Threads
-------

#. All thread-related functions have been removed in favor of the ``anyio.to_thread``
   and ``anyio.from_thread`` modules.
#. The ``@executor`` decorator has been dropped as incompatible with the new design, so
   it should be replaced with appropriate calls to :func:`anyio.to_thread.run_sync`. If
   you need to run an entire function in a thread, you can refactor it into a nested
   function on a coroutine function.

Replacing ``call_in_executor()`` and ``call_async()``
+++++++++++++++++++++++++++++++++++++++++++++++++++++

Before::

    from asyncio import Event
    from asphalt.core import call_async, call_in_executor

    def my_blocking_function(ctx: Context, event: Event) -> None:
        call_async(event.set)

    async def origin_async_func() -> None:
        event = Event()
        await call_in_executor(my_blocking_function, ctx, event)
        await event.wait()

After::

    from anyio import Event, from_thread, to_thread

    def my_blocking_function(event: Event) -> None:
        from_thread.run_sync(event.set)

    async def origin_async_func() -> None:
        event = Event()
        await to_thread.run_sync(some_blocking_function, arg1)
        await event.wait()

Replacing ``@executor``
+++++++++++++++++++++++

As there is no direct equivalent for ``@executor`` in AnyIO, you'll have to explicitly
run the function using :func:`anyio.to_thread.run_sync`.

Before::

    from asphalt.core.context import executor

    @executor
    def my_func():
        ...

    async def origin_async_func() -> None:
        await my_func()

After::

    from anyio import to_thread

    def my_func():
        ...

    async def origin_async_func() -> None:
        await to_thread.run_sync(my_func)

Replacing ``Context.threadpool()``
++++++++++++++++++++++++++++++++++

As there is no equivalent for ``Context.threadpool()`` in AnyIO, you need to place the
code that needs to be run in a thread in its own function, and then use
:func:`anyio.to_thread.run_sync` to run that function.

Before::

    async def my_func():
        var = 1
        async with threadpool():
            time.sleep(2)
            var = 2

After::

    from anyio import to_thread

    async def my_func():
        var = 1

        def wrapper():
            nonlocal var
            time.sleep(2)
            var = 2

        await to_thread.run_sync(wrapper)

Signals and events
------------------

In support of `structured concurrency`_, the signalling system (not to be confused with
operating system signals like ``SIGTERM`` et al), has been refactored to require the use
of context managers wherever possible.

Migrating custom event classes
++++++++++++++++++++++++++++++

As the :class:`Event` class no longer has an initializer, you need to remove the
``source`` and ``topic`` initializer parameters from your own subclasses, and drop the
``super().__init__(source, topic)`` call. You may also want to take this opportunity to
refactor them into data classes.

Before::

    from asphalt.core import Event

    class MyEvent(Event):
        def __init__(self, source, topic, an_attribute: str):
            super().__init__(source, topic)
            self.an_attribute = an_attribute

After::

    from dataclasses import dataclass

    from asphalt.core import Event

    @dataclass
    class MyEvent(Event):
        an_attribute: str

Iterating over events
+++++++++++++++++++++

As the ``connect()`` and ``disconnect()`` signal methods have been eliminated, you need
to use the :meth:`Signal.stream_events` method or

Before::

    from asphalt.core import Signal, Event

    class MyEvent(Event):
        ...

    class MyService:
        something = Signal(MyEvent)

    def event_listener(event: MyEvent) -> None:
        print("got an event")

    async def myfunc(service: MyService) -> None:
        service.something.connect(event_listener)
        ...
        service.something.disconnect(event_listener)

After::

    from asphalt.core import Signal, Event

    class MyEvent(Event):
        ...

    class MyService:
        something = Signal(MyEvent)

    async def myfunc(service: MyService) -> None:
        async with service.something.stream_events() as event_stream:
            async for event in event_stream:
                print("got an event")

Dispatching events
++++++++++++++++++

The :meth:`~Signal.dispatch` method has been changed to work like
``Signal.dispatch_raw()``. That is, you will need to pass it an appropriate
:class:`Event` object.

Before::

    from asphalt.core import Signal, Event

    class MyEvent(Event):
        def __init__(self, source, topic, an_attribute: str):
            super().__init__(source, topic)
            self.an_attribute = an_attribute

    class MyService:
        something = Signal(MyEvent)

    async def myfunc(service: MyService) -> None:
        service.something.dispatch("value")

After::

    from dataclasses import dataclass

    from asphalt.core import Signal, Event

    @dataclass
    class MyEvent(Event):
        an_attribute: str

    class MyService:
        something = Signal(MyEvent)

    async def myfunc(service: MyService) -> None:
        service.something.dispatch(MyEvent("value"))

Configuration
-------------

The ability to specify "shortcuts" using dots in the configuration keys has been
removed, as it interfered with logging configuration.

.. highlight:: yaml

Before::

    foo.bar: value

After::

    foo:
      bar: value

If your application uses two components of the same type, you've probably had to work
around the resource namespace conflicts with a configuration similar to this::

    my_component:
      foo: bar
    my_component_alter:
      type: my_component
      resource_name: alter
      foo: baz

On Asphalt 5, you can simplify this configuration::

    my_component:
      foo: bar
    my_component/alter:
      foo: baz

The slash in the key separates the component alias and the default resource name (which
is used in place of ``default``) when a component adds a resource during startup.

.. _AnyIO: https://github.com/agronholm/anyio/
.. _structured concurrency: https://en.wikipedia.org/wiki/Structured_concurrency
