.. highlight:: bash

Getting your feet wet: a simple echo server and client
======================================================

This tutorial will get you started with Asphalt development from the ground up.
You will be learn how to build a simple network server that echoes back messages sent to it, along
with a matching client application. It will however not yet touch more advanced concepts like
using the ``asphalt`` command to run an application with a configuration file.

Prerequisites
-------------

Asphalt requires Python 3.5.0 or later. You will also need to have the ``pip`` and ``virtualenv``
tools installed. The former should come with most Python installations, but if it does not, you can
usually install it with your operating system's package manager (``python3-pip`` is a good guess).
As for ``virtualenv``, you can install it with the ``pip install --user virtualenv`` command.

Setting up the virtual environment
----------------------------------

Now that you have your base tools installed, it's time to create a *virtual environment* (referred
to as simply ``virtualenv`` later). Installing Python libraries in a virtual environment isolates
them from other projects, which may require different versions of the same libraries.

Now, create a project directory and a virtualenv::

    mkdir tutorial
    cd tutorial
    virtualenv tutorialenv
    source tutorialenv/bin/activate

On Windows, the last line should be:

.. code-block:: doscon

   tutorialenv\Scripts\activate

The last command *activates* the virtualenv, meaning the shell will first look for commands in
its ``bin`` directory (``Scripts`` on Windows) before searching elsewhere. Also, Python will
now only import third party libraries from the virtualenv and not anywhere else. To exit the
virtualenv, you can use the ``deactivate`` command (but don't do that now!).

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

.. highlight:: python3

Now, let's write some code! Create a file named ``server.py`` in the ``echo`` package directory::

    from asphalt.core import Component, run_application


    class ServerComponent(Component):
        async def start(self, ctx):
            print('Hello, world!')

    if __name__ == '__main__':
        component = ServerComponent()
        run_application(component)

The ``ServerComponent`` class is the *root component* (and in this case, the only component) of
this application. Its ``start()`` method is called by ``run_application`` when it has
set up the event loop. Finally, the ``if __name__ == '__main__':`` block is not strictly necessary
but is good, common practice that prevents ``run_application()`` from being called again if this
module is ever imported from another module.

You can now try running the above application. With the project directory (``tutorial``) as your
current directory, do:

.. code-block:: bash

    python -m echo.server

This should print "Hello, world!" on the console. The event loop continues to run until you press
Ctrl+C (Ctrl+Break on Windows).

Making the server listen for connections
----------------------------------------

The next step is to make the server actually accept incoming connections.
For this purpose, the :func:`asyncio.start_server` function is a logical choice::

    from asyncio import start_server

    from asphalt.core import Component, run_application


    async def client_connected(reader, writer):
        message = await reader.readline()
        writer.write(message)
        writer.close()
        print('Message from client:', message.decode().rstrip())


    class ServerComponent(Component):
        async def start(self, ctx):
            await start_server(client_connected, 'localhost', 64100)

    if __name__ == '__main__':
        component = ServerComponent()
        run_application(component)

Here, :func:`asyncio.start_server` is used to listen to incoming TCP connections on the
``localhost`` interface on port 64100. The port number is totally arbitrary and can be changed to
any other legal value you want to use.

Whenever a new connection is established, the event loop launches ``client_connected()`` as a new
task. It receives two arguments, a :class:`~asyncio.StreamReader` and a
:class:`~asyncio.StreamWriter`. In the callback we read a line from the client, write it back to
the client and then close the connection. To get at least some output from our application, we
print the received message on the console (decoding it from ``bytes`` to ``str`` and stripping the
trailing newline character first). In production applications, you will want to use the
:mod:`logging` module for this instead.

If you have the ``netcat`` utility or similar, you can already test the server like this:

.. code-block:: bash

    echo Hello | nc localhost 64100

This command, if available, should print "Hello" on the console, as echoed by the server.

Creating the client
-------------------

No server is very useful without a client to access it, so we'll need to add a client module in
this project. And to make things a bit more interesting, we'll make the client accept a message to
be sent as a command line argument.

Create the file ``client.py`` file in the ``echo`` package directory as follows::

    import sys
    from asyncio import get_event_loop, open_connection

    from asphalt.core import Component, run_application


    class ClientComponent(Component):
        def __init__(self, message):
            self.message = message

        async def start(self, ctx):
            reader, writer = await open_connection('localhost', 64100)
            writer.write(self.message.encode() + b'\n')
            response = await reader.readline()
            writer.close()
            get_event_loop().stop()
            print('Server responded:', response.decode().rstrip())

    if __name__ == '__main__':
        msg = sys.argv[1]
        component = ClientComponent(msg)
        run_application(component)

In the client component, the message to be sent is first extracted from the list of command line
arguments. It is then given to ``ClientComponent`` as a constructor argument and saved as an
attribute of the component instance for later use in ``start()``.

When the client component starts, it connects to ``localhost`` on port 64100. Then it converts the
message to bytes for transport (adding a newline character so the server can use ``readline()``).
Then it reads a response line from the server. Finally, it closes the connection and stops the
event loop, allowing the application to exit.

To send the "Hello" message to the server, run this in the project directory:

.. code-block:: bash

    python -m echo.client Hello
