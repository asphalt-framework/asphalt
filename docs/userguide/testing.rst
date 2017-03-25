Testing Asphalt components
==========================

Testing Asphalt components and component hierarchies is a relatively simple procedure:

#. Create an instance of your :class:`~asphalt.core.component.Component`
#. Create a :class:`~asphalt.core.context.Context` instance
#. Run the component's ``start()`` method with the context as the argument
#. Run the tests
#. Close the context to release any resources

With Asphalt projects, it is recommended to use the `py.test`_ testing framework because it is
already being used with Asphalt core and it provides easy testing of asynchronous code
(via the pytest-asyncio_ plugin).

Example
-------

Let's build a test suite for the :doc:`Echo Tutorial <../tutorials/echo>`.

The client and server components could be tested separately, but to make things easier, we'll test
them against each other.

Create a ``tests`` directory at the root of the project directory and create a module named
``test_client_server`` there (the ``test_`` prefix is important)::

    import asyncio

    import pytest
    from asphalt.core import Context

    from echo.client import ClientComponent
    from echo.server import ServerComponent


    @pytest.fixture
    def event_loop():
        # Required on pytest-asyncio v0.4.0 and newer since the event_loop fixture provided by the
        # plugin no longer sets the global event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        yield loop
        loop.close()


    @pytest.fixture
    def context(event_loop):
        with Context() as ctx:
            yield ctx


    @pytest.fixture
    def server_component(event_loop, context):
        component = ServerComponent()
        event_loop.run_until_complete(component.start(context))


    def test_client(event_loop, server_component, context, capsys):
        client = ClientComponent('Hello!')
        event_loop.run_until_complete(client.start(context))
        exc = pytest.raises(SystemExit, event_loop.run_forever)
        assert exc.value.code == 0

        # Grab the captured output of sys.stdout and sys.stderr from the capsys fixture
        out, err = capsys.readouterr()
        assert out == 'Message from client: Hello!\nServer responded: Hello!\n'

The test module above contains one test function (``test_client``) and three fixtures:

* ``event_loop``: provides an asyncio event loop and closes it after the test
* ``context`` provides the root context and runs teardown callbacks after the test
* ``server_component``: creates and starts the server component

The client component is not provided as a fixture because, as always with
:class:`~asphalt.core.component.CLIApplicationComponent`, starting it would run the logic we want
to test, so we defer that to the actual test code.

In the test function (``test_client``), the client component is instantiated and started. Since the
component's ``start()`` function only kicks off the task that runs the client's business logic (the
``run()`` method), we have to wait until the task is complete by running the event loop (using
:meth:`~asyncio.AbstractEventLoop.run_forever`) until ``run()`` finishes and its callback code
attempts to terminate the application. For that purpose, we catch the resulting :exc:`SystemExit`
exception and verify that the application indeed completed successfully, as indicated by the return
code of 0.

Finally, we check that the server and the client printed the messages they were supposed to.
When the server receives a line from the client, it prints a message to standard output using
:func:`print`. Likewise, when the client gets a response from the server, it too prints out its
own message. By using pytest's built-in ``capsys`` fixture, we can capture the output and verify it
against the expected lines.

To run the test suite, make sure you're in the project directory and then do:

.. code-block:: bash

    pytest tests

For more elaborate examples, please see the test suites of various `Asphalt subprojects`_.

.. _py.test: http://pytest.org/
.. _pytest-asyncio: https://pypi.python.org/pypi/pytest-asyncio
.. _Asphalt subprojects: https://github.com/asphalt-framework
