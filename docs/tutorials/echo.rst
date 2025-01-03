Tutorial 1: Getting your feet wet – a simple echo server and client
===================================================================

.. py:currentmodule:: asphalt.core

This tutorial will get you started with Asphalt development from the ground up.
You will learn how to build a simple network server that echoes back messages sent to
it, along with a matching client application. It will however not yet touch more
advanced concepts like using the ``asphalt`` command to run an application with a
configuration file.

Prerequisites
-------------

Asphalt requires Python 3.8 or later. You will also need to have the ``venv`` module
installed for your Python version of choice. It should come with most Python
distributions, but if it does not, you can usually install it with your operating
system's package manager (``python3-venv`` is a good guess).

Setting up the virtual environment
----------------------------------

.. highlight:: bash

Now that you have your base tools installed, it's time to create a *virtual environment*
(referred to as simply ``virtualenv`` later). Installing Python libraries in a virtual
environment isolates them from other projects, which may require different versions of
the same libraries.

Now, create a project directory and a virtualenv::

    mkdir tutorial1
    cd tutorial1
    python -m venv tutorialenv
    source tutorialenv/bin/activate

On Windows, the last line should be:

.. code-block:: doscon

   tutorialenv\Scripts\activate

The last command *activates* the virtualenv, meaning the shell will first look for
commands in its ``bin`` directory (``Scripts`` on Windows) before searching elsewhere.
Also, Python will now only import third party libraries from the virtualenv and not
anywhere else. To exit the virtualenv, you can use the ``deactivate`` command (but
don't do that now!).

You can now proceed with installing Asphalt itself::

    pip install asphalt

Creating the project structure
------------------------------

Every project should have a top level package, so create one now::

    mkdir echo
    touch echo/__init__.py

On Windows, the last line should be:

.. code-block:: doscon

    copy NUL echo\__init__.py

Creating the first component
----------------------------

Now, let's write some code! Create a file named ``server.py`` in the ``echo`` package
directory:

.. literalinclude:: snippets/echo1.py
   :language: python
   :start-after: isort: off

The ``ServerComponent`` class is the *root component* (and in this case, the only
component) of this application. Its ``start()`` method is called by
:func:`run_application` when it has set up the event loop. Finally, the
``if __name__ == '__main__':`` block is not strictly necessary but is good, common
practice that prevents :func:`run_application()` from being called again if this module
is ever imported from another module.

You can now try running the above application. With the project directory
(``tutorial``) as your current directory, do:

.. code-block:: bash

    python -m echo.server

This should print "Hello, world!" on the console. The event loop continues to run until
you press Ctrl+C (Ctrl+Break on Windows).

Making the server listen for connections
----------------------------------------

The next step is to make the server actually accept incoming connections.
For this purpose, we will use AnyIO's :func:`~anyio.create_tcp_listener` function:

.. literalinclude:: ../../examples/tutorial1/echo/server.py
   :language: python
   :start-after: isort: off

Here, :func:`anyio.create_tcp_listener` is used to listen to incoming TCP connections on
the ``localhost`` interface on port 64100. The port number is totally arbitrary and can
be changed to any other legal value you want to use.

Whenever a new connection is established, the listener spawns a new task to run
``handle()``. Tasks work much like `green threads`_ in that they're adjourned when
waiting for something to happen and then resumed when the result is available. The main
difference is that a coroutine running in a task needs to use the ``await`` statement
(or ``async for`` or ``async with``) to yield control back to the event loop. In
``handle()``, the ``await`` on the first line will cause the task to be adjourned until
a packet has been received from the socket stream.

The ``handle()`` function receives a :class:`~anyio.abc.SocketStream` as the sole
argument. This object encapsulates the server side of the newly established TCP
connection. In ``handle()``, we read a single TCP packet from the client, write it back
to the client and then close the connection. To get at least some output from the
application, the function was made to print the received message on the console
(decoding it from ``bytes`` to ``str`` and stripping the trailing newline character
first). In production applications, you will want to use the :mod:`logging` module for
this instead.

If you have the ``netcat`` utility or similar, you can already test the server like
this::

    echo Hello | nc localhost 64100

This command, if available, should print "Hello" on the console, as echoed by the
server.

.. _green threads: https://en.wikipedia.org/wiki/Green_threads

Creating the client
-------------------

No server is very useful without a client to access it, so we'll need to add a client
module in this project. And to make things a bit more interesting, we'll make the client
accept a message to be sent as a command line argument.

Create the file ``client.py`` file in the ``echo`` package directory as follows:

.. literalinclude:: ../../examples/tutorial1/echo/client.py
   :language: python
   :start-after: isort: off

You may have noticed that ``ClientComponent`` inherits from
:class:`CLIApplicationComponent` instead of :class:`Component` and that instead of
overriding the :meth:`Component.start` method, :meth:`CLIApplicationComponent.run` is
overridden. This is standard practice for Asphalt applications that just do one specific
thing and then exit.

The script instantiates ``ClientComponent`` using the first command line argument as the
``message`` argument to the component's constructor. Doing this instead of directly
accessing ``sys.argv`` from the ``run()`` method makes this component easier to test and
allows you to specify the message in a configuration file (covered in the next
tutorial).

When the client component runs, it grabs the message to be sent from the list of command
line arguments (``sys.argv``), converts it from a unicode string to a bytestring and
adds a newline character (so the server can use ``readline()``). Then, it connects to
``localhost`` on port 64100 and sends the bytestring to the other end. Next, it reads a
response line from the server, closes the connection and prints the (decoded) response.
When the ``run()`` method returns, the application exits.

To send the "Hello" message to the server, run this in the project directory:

.. code-block:: bash

    python -m echo.client Hello

Conclusion
----------

This covers the basics of setting up a minimal Asphalt application. You've now learned
to:

* Create a virtual environment to isolate your application's dependencies from other
  applications
* Create a package structure for your application
* Start your application using :func:`~asphalt.core.run_application`
* Use `AnyIO socket streams`_ to implement a basic client-server protocol

This tutorial only scratches the surface of what's possible with Asphalt, however. The
:doc:`second tutorial <webnotifier>` will build on the knowledge you gained here and
teach you how to work with components, resources and configuration files to build more
useful applications.

.. _AnyIO socket streams: https://anyio.readthedocs.io/en/stable/networking.html#\
    working-with-tcp-sockets
