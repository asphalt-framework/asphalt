Working with coroutines and threads
===================================

.. py:currentmodule:: asphalt.core

Asphalt was designed as a network oriented framework capable of high concurrency. This means that
it can efficiently work with hundreds or even thousands of connections at once. This is achieved by
utilizing `co-operative multitasking`_, using an *event loop* provided by the :mod:`asyncio`
module.

The event loop can only work on one task at a time, so whenever the currently running task needs to
wait for something to happen, it will need to explicitly yield control back to the event loop
(using ``await`` and similar statements) to let the event loop run other tasks while this task
waits for the result. Once the result is available, the event loop will resume the task.

There is another concurrency mechanism called *threads*. Threads are an implementation of
`preemptive multitasking`_, which means that the CPU may run your program at more than one location
at once and your code will not have to worry about yielding control to another task. There are some
big downsides to using threads, however. First off, threaded code is much more prone to
`race conditions`_ and programs often need to use `locks`_ to share state in a predictable manner.
Second, threads don't scale. When you have more threads than CPU cores, the cores need to do
`context switching`_, that is, juggle between the threads. With a large number of threads, the
overhead from context switching becomes very significant up to the point where the system stops
responding altogether.

While Asphalt was designed to avoid the use of threads, they are sometimes necessary.
Most third party libraries at the moment don't support the asynchronous concurrency model, and as
such, they sometimes need to be used with threads in order to avoid blocking the event loop.
Also, file operations cannot, at this time, be executed asynchronously and need to be wrapped in
threads. Finally, your application might just need to do some CPU heavy processing that would
otherwise block the event loop for long periods of time.

To help with this, Asphalt contains functionality with which you can easily run code in thread
pools or call asynchronous code from worker threads.

Examples
--------

Consider a coroutine function that reads the contents of a certain file and then sends them over a
network connection. While you might get away with reading the file in the event loop thread,
consider what happens if the disk has to spin up from idle state or the file is located on a slow
(or temporarily inaccessible) network drive. The whole event loop will then be blocked for who
knows how long.

The easiest way is probably to use :func:`~asphalt.core.context.call_in_executor`::

    from pathlib import Path

    async def read_and_send_file(ctx, connection):
        contents = await ctx.call_in_executor(Path('file.txt').read_bytes)
        await connection.send(contents)

You can also opt to execute entire blocks with a thread pool executor by using
:func:`~asphalt.core.context.threadpool`::

    async def read_and_send_file(ctx, connection):
        async with ctx.threadpool():
            # Anything inside this block runs in a worker thread!
            contents = Path('file.txt').read_bytes()

        # Don't try to "await" inside the ctx.threadpool() block!
        await connection.send(contents)

Alternatively, you can run the whole function in an executor.
You will then need to make it a regular function instead of a coroutine function::

    from asphalt.core import executor

    @executor
    def read_and_send_file(ctx, connection):
        contents = Path('file.txt').read_bytes()
        ctx.call_async(connection.send, contents)

Using alternate executors
-------------------------

By default, all these methods use the default executor of the event loop, which in turn defaults to
a :class:`~concurrent.futures.ThreadPoolExecutor` with the default number of workers.
Sometimes you may encounter situations where you need to use multiple executors, each earmarked
for a particular task or group of tasks so as to prevent other tasks from getting stuck due to the
lack of available workers. To this end, the mechanisms described above can be made to target a
specific executor, either given directly or acquired as a resource from a context.

Suppose you add an ``Executor`` resource named ``file_ops`` to a context::

    from concurrent.futures import ThreadPoolExecutor, Executor

    file_ops = ThreadPoolExecutor(5)  # max 5 worker threads for file operations
    ctx.add_resource(file_ops, 'file_ops', types=[Executor])

You can then use this executor resource by its name::

    async def read_and_send_file(ctx, connection):
        contents = await ctx.call_in_executor(Path('file.txt').read_bytes, executor='file_ops')
        await connection.send(contents)

Also works with the async context manager::

    async def read_and_send_file(ctx, connection):
        async with ctx.threadpool('file_ops'):
            contents = Path('file.txt').read_bytes()

        await connection.send(contents)

And of course as a decorator too, as long as the context is provided::

    from asphalt.core import executor

    @executor('file_ops')
    def read_and_send_file(ctx, connection):
        contents = Path('file.txt').read_bytes()
        ctx.call_async(connection.send, contents)

.. _co-operative multitasking: https://en.wikipedia.org/wiki/Cooperative_multitasking
.. _preemptive multitasking: https://en.wikipedia.org/wiki/Preemption_%28computing%29
.. _race conditions: https://en.wikipedia.org/wiki/Race_condition
.. _locks: https://en.wikipedia.org/wiki/Lock_%28computer_science%29
.. _context switching: https://en.wikipedia.org/wiki/Context_switch
