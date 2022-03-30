Testing Asphalt components
==========================

.. py:currentmodule:: asphalt.core

Testing Asphalt components and component hierarchies is a relatively simple procedure:

#. Create an instance of your :class:`~asphalt.core.component.Component`
#. Create a :class:`~asphalt.core.context.Context` instance
#. Run the component's :meth:`~.component.Component.start` method with the context as the argument
#. Run the tests
#. Close the context to release any resources

With Asphalt projects, it is recommended to use the pytest_ testing framework because it is
already being used with Asphalt core and it provides easy testing of asynchronous code
(via the pytest-asyncio_ plugin).

Example
-------

Let's build a test suite for the :doc:`Echo Tutorial <../tutorials/echo>`.

The client and server components could be tested separately, but to make things easier, we'll test
them against each other.

Create a ``tests`` directory at the root of the project directory and create a module named
``test_client_server`` there (the ``test_`` prefix is important)::

    import pytest
    from asphalt.core import Context

    from echo.client import ClientComponent
    from echo.server import ServerComponent


    def test_client_and_server(event_loop, capsys):
        async def run():
            async with Context() as ctx:
                server = ServerComponent()
                await server.start(ctx)

                client = ClientComponent("Hello!")
                await client.start(ctx)

        event_loop.create_task(run())
        with pytest.raises(SystemExit) as exc:
            event_loop.run_forever()

        assert exc.value.code == 0

        # Grab the captured output of sys.stdout and sys.stderr from the capsys fixture
        out, err = capsys.readouterr()
        assert out == "Message from client: Hello!\nServer responded: Hello!\n"

The test module above contains one test function which uses two fixtures:

* ``event_loop``: comes from pytest-asyncio_; provides an asyncio event loop
* ``capsys``: captures standard output and error, letting us find out what message the components
  printed

In the test function (``test_client_and_server()``), the server and client components are
instantiated and started. Since the client component's
:meth:`~.component.CLIApplicationComponent.start` function only kicks off a task that runs the
client's business logic (the :meth:`~.component.CLIApplicationComponent.run` method), we have to
wait until the task is complete by running the event loop (using
:meth:`~asyncio.loop.run_forever`) until
:meth:`~.component.CLIApplicationComponent.run` finishes and its callback code attempts to
terminate the application. For that purpose, we catch the resulting :exc:`SystemExit` exception and
verify that the application indeed completed successfully, as indicated by the return code of 0.

Finally, we check that the server and the client printed the messages they were supposed to.
When the server receives a line from the client, it prints a message to standard output using
:func:`print`. Likewise, when the client gets a response from the server, it too prints out its
own message. By using pytest's built-in capsys_ fixture, we can capture the output and verify it
against the expected lines.

To run the test suite, make sure you're in the project directory and then do:

.. code-block:: bash

    PYTHONPATH=. pytest tests

For more elaborate examples, please see the test suites of various `Asphalt subprojects`_.

.. _pytest: http://pytest.org/
.. _pytest-asyncio: https://pypi.python.org/pypi/pytest-asyncio
.. _capsys: https://docs.pytest.org/en/6.2.x/capture.html#accessing-captured-output-from-a-test-function
.. _Asphalt subprojects: https://github.com/asphalt-framework
