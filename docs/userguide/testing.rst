Testing Asphalt components
==========================

Testing Asphalt components and component hierarchies is a relatively simple procedure:

1. Create an instance of your :class:`~asphalt.core.component.Component`
1. Create a :class:`~asphalt.core.context.Context` instance
1. Run the component's ``start()`` method with the context as the argument
1. Run the tests
1. Dispatch the ``finished`` event on the context to release any resources

With Asphalt projects, it is recommended to use the `py.test`_ testing framework because it is
already being used with Asphalt core and it provides easy testing of asynchronous code
(via the pytest-asyncio_ plugin).

Example
-------

Given this example component::

    import asyncio

    from asphalt.core import Component


    class RemoteServer:
        def __init__(reader, writer):
            self.reader = reader
            self.writer = writer

        async def ping():
            self.writer.write(b'PING')
            line = await self.reader.readline()
            return line.rstrip()

        def close():
            self.writer.close()

    class RemoteConnectionComponent(Component):
        def __init__(host: str, port: int):
            self.host = host
            self.port = port

        async def start(ctx: Context):
            # Open a TCP connection to the remote host
            reader, writer = await asyncio.open_connection(self.host, self.port)
            server = RemoteServer(reader, writer)

            # Make the RemoteServer instance available as ctx.server
            await ctx.publish_resource(server, context_attr='server')

            # Close the connection when the context is finished
            ctx.add_listener('finished', lambda ctx: server.close())

You could test it using `py.test`_ like this::

    import pytest
    from asphalt.core import Context


    @pytest.yield_fixture
    def context(event_loop):
        # The event_loop fixture is provided by pytest-asyncio
        ctx = Context()

        # This is where this fixture will adjourn until the test(s) are done
        yield ctx

        # This is run at test teardown
        event_loop.run_until_complete(ctx.dispatch_event('finished', None))


    @pytest.fixture
    def component(event_loop, context):
        component = RemoteConnectionComponent()
        event_loop.run_until_complete(component.start(context))


    @pytest.mark.asyncio
    async def test_my_component(component, context):
        # We declare a dependency on "component" to cause its fixture to be run
        reply = await context.server.ping()
        assert reply == b'PONG'

Just remember to use ``@pytest.mark.asyncio`` on every test function that's a coroutine.

For more elaborate examples, please see the test suites of various `Asphalt subprojects`_.

.. _py.test: http://pytest.org/
.. _pytest-asyncio: https://pypi.python.org/pypi/pytest-asyncio
.. _Asphalt subprojects: https://github.com/asphalt-framework
