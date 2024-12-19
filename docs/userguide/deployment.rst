Configuration and deployment
============================

.. py:currentmodule:: asphalt.core

As your application grows more complex, you may find that you need to have different
settings for your development environment and your production environment. You may even
have multiple deployments that all need their own custom configuration.

For this purpose, Asphalt provides a command line interface that will read a YAML_
formatted configuration file and run the application it describes.

.. _YAML: https://yaml.org/

Running the Asphalt launcher
----------------------------

Running the launcher is very straightfoward:

.. code-block:: bash

    asphalt run [yourconfig.yaml your-overrides.yml...] [--set path.to.key=val]

Or alternatively:

.. code-block:: bash

    python -m asphalt run [yourconfig.yaml your-overrides.yml...] [--set path.to.key=val]

What this will do is:

#. read all the given configuration files, if any, starting from ``yourconfig.yaml``
#. read the command line configuration options passed with ``--set``, if any
#. merge the configuration files' contents and the command line configuration options
   into a single configuration dictionary using :func:`merge_config`.
#. call :func:`run_application` using the configuration dictionary as keyword
   arguments

Writing a configuration file
----------------------------

.. highlight:: yaml

A production-ready configuration file should contain at least the following options:

* ``component``: a dictionary containing the class name and keyword arguments for its
  initializer
* ``logging``: a dictionary to be passed to :func:`logging.config.dictConfig`

Suppose you had the following component class as your root component::

    class MyRootComponent(Component):
        def __init__(self, data_directory: str):
            self.data_directory = data_directory
            self.add_component('mailer', backend='smtp')
            self.add_component('sqlalchemy')

You could then write a configuration file like this::

    ---
    max_threads: 20
    component:
      type: !!python/name:myproject.MyRootComponent
      data_directory: /some/file/somewhere
      components:
        mailer:
          host: smtp.mycompany.com
          ssl: true
        sqlalchemy:
          url: postgresql:///mydatabase

    logging:
      version: 1
      disable_existing_loggers: false
      handlers:
        console:
          class: logging.StreamHandler
          formatter: generic
      formatters:
        generic:
          format: "%(asctime)s:%(levelname)s:%(name)s:%(message)s"
      root:
        handlers: [console]
        level: INFO

In the above configuration you have three top level configuration keys: ``max_threads``,
``component`` and ``logging``, all of which are directly passed to
:func:`run_application` as keyword arguments.

The ``component`` section defines the type of the root component using the specially
processed ``type`` option. You can either specify a setuptools entry point name (from
the ``asphalt.components`` namespace) or a text reference like ``module:class`` (see
:func:`resolve_reference` for details). The rest of the keys in this section are
passed directly to the constructor of the ``MyRootComponent`` class.

The ``components`` section within ``component`` is processed in a similar fashion.
Each subsection here is a component type alias and its keys and values are the
constructor arguments to the relevant component class. The per-component configuration
values are merged with those provided in the ``start()`` method of ``MyRootComponent``.
See the next section for a more elaborate explanation.

With ``max_threads: 20``, the maximum number of threads that functions like
:func:`anyio.to_thread.run_sync` can have running, to 20.

The ``logging`` configuration tree here sets up a root logger that prints all log
entries of at least ``INFO`` level to the console. You may want to set up more granular
logging in your own configuration file. See the
:ref:`Python standard library documentation <python:logging-config-dictschema>` for
details.

Using multiple components of the same type under the same parent component
--------------------------------------------------------------------------

Occasionally, you may run into a situation where you need two instances of the same
component under the same parent component. A practical example of this is two SQLAlchemy
engines, one for the master, one for the replica database. But specifying this in the
configuration won't work::

    ---
    component:
      type: !!python/name:myproject.MyRootComponent
      components:
        sqlalchemy:
          url: postgresql://user:pass@postgres-master/dbname
        sqlalchemy:
          url: postgresql://user:pass@postgres-replica/dbname

Not only is there a conflict as you can't have two identical aliases within the same
parent, but even if you could start the component tree like this, the two SQLALchemy
components would try to publish resources with the same type and name combinations.

The solution to the problem is to use different aliases::

    ---
    component:
      type: !!python/name:myproject.MyRootComponent
      components:
        sqlalchemy:
          url: postgresql://user:pass@postgres-master/dbname
        sqlalchemy/replica:
          url: postgresql://user:pass@postgres-replica/dbname

As of v5.0, the framework understands the ``component-type/resource-name`` notation, and
fills in the ``type`` field of the child component with ``sqlalchemy``, if there's no
existing ``type`` key.

With this configuration, you get two distinct SQLAlchemy components, and the second one
will publish its engine and session factory resources using the ``replica`` name rather
than ``default``.

.. note:: The altered resource name only applies to the :meth:`Component.start` method,
    **not** :meth:`Component.prepare`, as the latter is meant to provide resources to
    child components, and they would need to know beforehand what resource name to
    expect.

Using data from environment variables and files
-----------------------------------------------

Many deployment environments (Kubernetes, Docker Swarm, Heroku, etc.) require
applications to input configuration values and/or secrets using environment variables or
external files. To support this, Asphalt extends the YAML parser with three custom tags:

* ``!Env``: substitute with the value of an environment variable
* ``!TextFile`` substitute with the contents of a (UTF-8 encoded) text file (as ``str``)
* ``!BinaryFile`` substitute with the contents of a file (as ``bytes``)

For example::

    ---
    component:
      type: !!python/name:myproject.MyRootComponent
      param_from_environment: !Env MY_ENV_VAR
      files:
        - !TextFile /path/to/file.txt
        - !BinaryFile /path/to/file.bin

If a file path contains spaces, you can just quote it::

    ---
    component:
      type: !!python/name:myproject.MyRootComponent
      param_from_text_file: !TextFile "/path with spaces/to/file.txt"

.. note:: This does **not** allow you to include other YAML documents as part of the
    configuration, except as text/binary blobs. See the next section if this is what you
    want.

.. versionadded:: 4.5.0

Configuration overlays
----------------------

Component configuration can be specified on several levels:

* Hard-coded arguments to :meth:`Component.add_component`
* First configuration file argument to ``asphalt run``
* Second configuration file argument to ``asphalt run``
* ...
* Command line configuration options to ``asphalt run --set``

Any options you specify on each level override or augment any options given on previous
levels. The command line configuration options have precedence over the configuration
files. To minimize the effort required to build a working configuration file for your
application, it is suggested that you pass as many of the options directly in the
component initialization code and leave only deployment specific options like API keys,
access credentials and such to the configuration file.

With the configuration presented in the earlier paragraphs, the ``mailer`` component's
constructor gets passed three keyword arguments:

* ``backend='smtp'``
* ``host='smtp.mycompany.com'``
* ``ssl=True``

The first one is provided in the root component code while the other two options come
from the YAML file. You could also override the mailer backend in the configuration file
if you wanted, or at the command line (with the configuration file saved as
``config.yaml``):

.. code-block:: bash

    asphalt run config.yaml --set component.components.mailer.backend=sendmail

.. note::
    Note that if you want a ``.`` to be treated as part of an identifier, and not as a
    separator, you need to escape it at the command line with ``\``. For instance, in
    both commands:

    .. code-block:: bash

        asphalt run config.yaml --set "logging.loggers.asphalt\.templating.level=DEBUG"
        asphalt run config.yaml --set logging.loggers.asphalt\\.templating.level=DEBUG

    The logging level for the ``asphalt.templating`` logger will be set to ``DEBUG``.

The same effect can be achieved programmatically by supplying the override configuration
to the container component via its ``components`` constructor argument. This is very
useful when writing tests against your application. For example, you might want to use
the ``mock`` mailer in your test suite configuration to test that the application
correctly sends out emails (and to prevent them from actually being sent to
recipients!).

Defining multiple services
--------------------------

.. versionadded:: 4.1.0

Sometimes it may be more convenient to use a single configuration file for launching
your application with different configurations or entry points. To this end, the runner
supports the notion of "service definitions" in the configuration file. This is done by
replacing the ``component`` dictionary with a ``services`` dictionary at the top level
of the configuration file and either setting the ``ASPHALT_SERVICE`` environment
variable or by passing the ``--service`` (or ``-s``) option when launching the runner.
This approach provides the additional advantage of allowing the use of YAML references,
like so::

    ---
    services:
      server:
        max_threads: 30
        component:
          type: !!python/name:myproject.server.ServerComponent
          components:
            wamp: &wamp
              host: wamp.example.org
              port: 8000
              tls: true
              auth_id: serveruser
              auth_secret: serverpass
            mailer:
              backend: smtp

      client:
        component:
          type: !!python/name:myproject.client.ClientComponent
          components:
            wamp:
              <<: *wamp
              auth_id: clientuser
              auth_secret: clientpass

Each section under ``services`` is like its own distinct top level configuration.
Additionally, the keys under each service are merged with any top level configuration,
so you can, for example, define a logging configuration there.

Now, to run the ``server`` service, do:

.. code-block:: bash

    asphalt run -s server config.yaml

The ``client`` service is run in the same fashion:

.. code-block:: bash

    asphalt run -s client config.yaml

You can also define a service with a special name, ``default``, which is used in case
multiple services are present and no service has been explicitly selected.

.. note:: The ``-s/--service`` command line switch overrides the ``ASPHALT_SERVICE``
    environment variable.

Performance tuning
------------------

When you want maximum performance, you'll also want to use the fastest available event
loop implementation. If you're running on the asyncio backend (the default), you can
get a nice performance boost by enabling uvloop_ (assuming it's installed).
Add the following piece to your application's configuration:

.. code-block:: yaml

    backend_options:
      use_uvloop: true

.. _uvloop: https://magic.io/blog/uvloop-make-python-networking-great-again/
