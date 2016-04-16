Working with coroutines and threads
===================================

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

To help with this, the `asyncio_extras`_ library was created as a byproduct of Asphalt.
It provides several conveniences you can use to easily use threads when the need arises.

Examples
--------

Consider a coroutine function that reads the contents of a certain file and then sends them over a
network connection. While you might get away with reading the file in the event loop thread,
consider what happens if the disk has to spin up from idle state or the file is located on a slow
(or temporarily inaccessible) network drive. The whole event loop will then be blocked for who
knows how long.

The easiest way is probably to use :func:`~asyncio_extras.file.open_async`::

    from asyncio_extras import open_async

    async def read_and_send_file(connection):
        async with open_async('file.txt', 'rb') as f:
            contents = await f.read()

        await connection.send(contents)

The following snippet achieves the same goal::

    from asyncio_extras import threadpool

    async def read_and_send_file(connection):
        async with threadpool():
            with open('file.txt', 'rb') as f:
                contents = f.read()

        await connection.send(contents)

As does the next one::

    from asyncio_extras import call_in_executor

    async def read_and_send_file(connection):
        f = await call_in_executor(open, 'file.txt', 'rb')
        with f:
            contents = await call_in_executor(f.read)

        await connection.send(contents)

Alternatively, you can run the whole function in the thread pool.
You will need to make it a regular function instead of a coroutine function and you must
explicitly pass in the event loop object::

    from asyncio_extras import threadpool, call_async

    @threadpool
    def read_and_send_file(connection, loop):
        with open('file.txt', 'rb') as f:
            contents = f.read()

        call_async(loop, connection.send, contents)

Using alternate thread pools
----------------------------

In more advanced applications, you may find it useful to set up specialized thread pools for
certain tasks in order to avoid the default thread pool from being overburdened::

    from concurrent.futures import ThreadPoolExecutor

    from asyncio_extras import threadpool

    file_ops = ThreadPoolExecutor(5)  # max 5 threads for file operations


    async def read_and_send_file(connection):
        async with threadpool(file_ops):
            with open('file.txt', 'rb') as f:
                contents = f.read()

        await connection.send(contents)


All the thread related utilities in `asyncio_extras`_ have a way to specify the executor to use.
Refer to its documentation for the specifics.


.. _co-operative multitasking: https://en.wikipedia.org/wiki/Cooperative_multitasking
.. _preemptive multitasking: https://en.wikipedia.org/wiki/Preemption_%28computing%29
.. _race conditions: https://en.wikipedia.org/wiki/Race_condition
.. _locks: https://en.wikipedia.org/wiki/Lock_%28computer_science%29
.. _context switching: https://en.wikipedia.org/wiki/Context_switch
.. _asyncio_extras: https://pypi.python.org/pypi/asyncio_extras
