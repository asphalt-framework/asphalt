Working with tasks and threads
==============================

.. py:currentmodule:: asphalt.core

Asphalt, as of version 5, leverages AnyIO_ to provide both `structured concurrency`_ and
support for multiple asynchronous event loop implementations (asyncio and Trio, as of
this writing). It is therefore highly recommended that you use AnyIO, rather than
asyncio, for your task management, synchronization, concurrency and file I/O needs
whenever possible. This ensures compatibility with AnyIO's "level cancellation" model,
and potentially enables you to switch to a different event loop implementation, should
the need arise.

Working with asynchronous tasks
-------------------------------

The main idea behind `structured concurrency`_ is that each task must have a parent task
watching over it, and if a task raises an exception that is not handled, then the
exception propagates to the parent task, taking all the other tasks down with it until
the exception is finally handled somewhere, or the entire process crashes. This ensures
that tasks never crash quietly, as is often the case with asyncio applications using
:func:`~asyncio.create_task` to spawn "fire-and-forget" tasks.

To enable Asphalt users to work with tasks while respecting structured concurrency, two
ways of launching tasks are provided: **service tasks** and
**background task factories**.

Service tasks
+++++++++++++

Service tasks are started to last throughout the lifespan of the context.
They're typically used to run network services like web apps.

When the context is torn down, service tasks will also be shut down. By default, they
will be cancelled, but you can control this behavior by passing a different value as
``teardown_action``:

* ``cancel`` (the default): cancel the task and wait for it to finish
* a callable: run the given callable at teardown to trigger the task to shut itself down
  (and wait for the task to finish on its own)
* ``None``: do nothing and just wait for the task to finish on its own

If a service task crashes, it will take down the whole application with it. This is part
of the `structured concurrency`_ design that is intended to ensure that failures don't
go unnoticed. You're responsible for catching and handling any exceptions raised in
service tasks.

Background task factories
+++++++++++++++++++++++++

Background task factories are meant to be used for dynamically spawning background tasks
on demand. A typical example would be a web app request handler that needs to send an
email, but wants to return a response to the end user straight away. The background
task will thus outlive the task that was spawned to handle the request.

In contrast to service tasks, any running background task will block the teardown of
the task factory from which they were spawned.

By default, an unhandled exception raised in a task spawned from a background task
factory will propagate and then crash the entire application. You can, however, control
this behavior by passing a callable as ``exception_handler`` to
:func:`start_background_task_factory`. If a background task crashes, the exception
handler is called with the exception as the sole argument. If the handler returns a
truthy value, the exception is ignored. In all other cases it is reraised.

Working with threads
--------------------

Threads are usually used when calling functions that take a long time (more than tens of
milliseconds) to execute, either because they use significant amounts of CPU time, or
they access external devices. The recommended way to use threads from asynchronous code
is to use :func:`anyio.to_thread.run_sync`::

    from functools import partial

    from anyio import to_thread

    def my_synchronous_func(a: int, b: int, *, kwarg: str = "") -> int:
        ...

    async def my_async_func() -> None:
        retval = await to_thread.run_sync(my_synchronous_func, 2, 5)

        # Use partial() if you need to pass keyword arguments
        retval = await to_thread.run_sync(partial(my_synchronous_func, 3, 6, kwarg="foo"))

.. seealso:: To learn more about working with threads using AnyIO's APIs, see the
    :doc:`anyio:threads` section in AnyIO's documentation.

Configuring the maximum amount of worker threads
++++++++++++++++++++++++++++++++++++++++++++++++

The configuration option ``max_threads`` sets the limit on how many worker threads will
at most be used with :func:`anyio.to_thread.run_sync` if no explicit capacity limiter
is passed.

For example, this YAML configuration will set the thread limit to 60 in the default
capacity limiter:

.. code-block:: yaml

    max_threads: 60
    component:
        ...

.. note:: This will **not** affect backend-specific thread APIs like
    :func:`asyncio.to_thread` or :meth:`asyncio.loop.run_in_executor`!

.. _AnyIO: https://github.com/agronholm/anyio/
.. _structured concurrency: https://en.wikipedia.org/wiki/Structured_concurrency
