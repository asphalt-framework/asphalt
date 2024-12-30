Testing Asphalt components
==========================

.. py:currentmodule:: asphalt.core

Testing Asphalt components and component hierarchies is a relatively simple procedure:

#. Create a :class:`~Context` and enter it with ``async with ...``
#. Run :func:`start_component` with your component class as the first argument (and the
   configuration dictionary, if you have one, as the second argument)
#. Run the test code itself

With Asphalt projects, it is recommended to use the pytest_ testing framework because it
is already being used with Asphalt core and it provides easy testing of asynchronous
code (via `AnyIO's pytest plugin`_).

Example
-------

Let's build a test suite for the :doc:`Echo Tutorial <../tutorials/echo>`.

The client and server components could be tested separately, but to make things easier,
we'll test them against each other.

Create a ``tests`` directory at the root of the project directory and create a module
named ``test_client_server`` there (the ``test_`` prefix is important):

.. literalinclude:: ../../examples/tutorial1/tests/test_client_server.py
   :language: python

In the above test module, the first thing you should note is
``pytestmark = pytest.mark.anyio``. This is the pytest marker that marks all coroutine
functions in the module to be run via AnyIO's pytest plugin.

The next item in the module is the ``server`` asynchronous generator fixture. Fixtures
like these are run by AnyIO's pytest plugin in their respective tasks, making the
practice of straddling a :class:`Context` on a ``yield`` safe. This would normally be
bad, as the context contains a :class:`~anyio.abc.TaskGroup` which usually should not be
used together with ``yield``, unless it's carefully managed like it is here.

The actual test function, ``test_client_and_server()`` first declares a dependency
on the ``server`` fixture, and then on another fixture (capsys_). This other fixture is
provided by ``pytest``, and it captures standard output and error, letting us find out
what message the components printed. Note that the ``server`` fixture also depends on
this fixture so that outputs from both the server and client are properly captured.

In this test function, the client component is instantiated and run. Because the client
component is a :class:`CLIApplicationComponent`, we can just run it directly by calling
its ``run()`` method. While the client component does not contain any child components
or other startup logic, we're nevertheless calling its ``start()`` method first, as this
is a "best practice".

Finally, we exit the context and check that the server and the client printed the
messages they were supposed to. When the server receives a line from the client, it
prints a message to standard output using :func:`print`. Likewise, when the client gets
a response from the server, it too prints out its own message. By using the capsys_
fixture, we can capture the output and verify it against the expected lines.

To run the test suite, make sure you're in the project directory and then do:

.. code-block:: bash

    PYTHONPATH=. pytest tests

For more elaborate examples, please see the test suites of various
`Asphalt subprojects`_.

.. _pytest: https://pytest.org/
.. _AnyIO's pytest plugin: https://anyio.readthedocs.io/en/stable/testing.html
.. _capsys: https://docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html\
   #accessing-captured-output-from-a-test-function
.. _Asphalt subprojects: https://github.com/asphalt-framework
