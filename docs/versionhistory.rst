Version history
===============

This library adheres to `Semantic Versioning 2.0 <http://semver.org/>`_.

**4.10.0** (2022-05-27)

- Changed ``@executor`` to propagate the `PEP 567`_ context to the worker thread, just
  like ``Context.call_in_executor()``

**4.9.1** (2022-05-22)

- Fixed type annotation for ``@context_teardown``
- Improved type annotations

**4.9.0** (2022-05-22)

- Added ``asphalt.core.get_resources()`` as a top-level shortcut for
  ``current_context().get_resources(...)``
- Allowed resource retrieval and generation in teardown callbacks until the context has
  been completely closed (this would previously raise
  ``RuntimeError("this context has already been closed")``)
- Allowed specifying optional dependencies with dependency injection, using either
  ``Optional[SomeType]`` (all Python versions) or ``SomeType | None`` (Python 3.10+)
- Allowed omitting the ``types`` argument in ``Context.add_resource_factory()`` if the
  factory callable has a return type annotation
- ``Context.call_in_executor()`` now copies the current `PEP 567`_ context to the worker
  thread, allowing operations that require the "current context" to be present (e.g.
  dependency injection)
- Raise better errors when the developer forgets to call ``resource()`` or forgets to
  add the ``@inject`` decorator
- Raise a ``UserWarning`` when ``@inject`` is used on a function that has no
  ``resource()`` declarations
- Fixed ``@inject`` not resolving forward references in type annotations in locally
  defined functions
- Improved type annotations

.. _PEP 567: https://peps.python.org/pep-0567/

**4.8.0** (2022-04-28)

- ``Context`` now accepts parametrized generic classes as resource types
- Deprecated context variables in favor of dependency injection
- Replaced ``asphalt.core.context.Dependency`` with
  ``asphalt.core.context.resource`` due to issues with strict type checking (the former
  is now deprecated and will be removed in v5.0)
- Added support for dependency injection on synchronous functions
- Added shortcut functions for obtaining resources (``asphalt.core.get_resource()``,
  ``asphalt.core.require_resource()``)
- Fixed dependency injection not working with forward references
  (``from __future__ import annotations``)

**4.7.0** (2022-04-08)

- Removed all uses of Python 3.5 style ``await yield_()`` from core code and documentation
- Added tracking of current Asphalt context in a :pep:`555` context variable, available via
  ``current_context()``
- Added dependency injection in coroutine functions via ``Dependency()`` and ``inject()``

**4.6.0** (2021-12-15)

- Added support for Python 3.10
- Dropped support for Python 3.5 and 3.6
- Removed all uses of the ``loop`` argument (fixes Python 3.10 compatibility)
- Switched to v0.15+ API of ``ruamel.yaml``
- Switched from ``pkg_resources`` to ``importlib.metadata`` for loading entry points
- Fixed ``DeprecationWarning`` about passing coroutine objects to ``asyncio.wait()``
- Fixed ``DeprecationWarning`` about implicitly creating a new event loop using
  ``get_event_loop()``
- Added the ``py.typed`` marker to enable type checking with dependent projects
- Deprecated the use of ``Context`` as a synchronous context manager

**4.5.0** (2019-03-26)

- Added new custom YAML tags (``!Env``, ``!BinaryFile`` and ``!TextFile``)

**4.4.4** (2018-05-08)

- Changed the ``async_timeout`` dependency to allow the 3.x and newer releases

**4.4.3** (2018-02-05)

- Fixed exception in ``stream_events()`` cleanup code introduced in the previous release

**4.4.2** (2018-02-02)

- Fixed memory leak when ``stream_events()`` is called but the returned generator is never used

**4.4.1** (2018-01-21)

- Fixed incompatibility with Python 3.5.2

**4.4.0** (2017-11-25)

- Removed the requirement for async generators to yield at least once when wrapped with
  ``@context_teardown``
- Removed aiogevent support since it has been removed from PyPI

**4.3.0** (2017-11-05)

- The runner now calls ``logging.shutdown()`` after the event loop has been closed
- Added the ``Context.get_resources()`` method
- Made ``stream_events()`` connect to the signal when called instead of the first iteration of the
  async generator

**4.2.0** (2017-08-24)

- Allowed selecting the service to run with ``asphalt run`` using an environment variable
  (``ASPHALT_SERVICE``)

**4.1.0** (2017-08-18)

- Added support for the `Tokio <https://github.com/PyO3/tokio>`_ event loop
- Added a feature to the runner that lets one define multiple services in a configuration file and
  select which one to run
- Increased the runner default start timeout to 10 seconds

**4.0.0** (2017-06-04)

- **BACKWARD INCOMPATIBLE** When a teardown callback raises an exception during
  ``Context.close()``, a ``TeardownException`` is raised at the end instead of the error being
  logged
- Renamed the ``asphalt.core.command`` module to ``asphalt.core.cli``
- Fixed the inability to override the component type from external configuration
  (contributed by Devin Fee)

**3.0.2** (2017-05-05)

- Fixed ``CLIApplicationComponent`` running prematurely (during the application start phase) and
  skipping the proper shutdown sequence
- Fixed return code from ``CLIApplicationComponent`` being ignored

**3.0.1** (2017-04-30)

- Fixed ``run_application()`` not working on Windows due to ``NotImplementedError`` when adding the
  ``SIGTERM`` signal handler

**3.0.0** (2017-04-10)

- **BACKWARD INCOMPATIBLE** Upped the minimum Python version to 3.5.2 from 3.5.0
- **BACKWARD INCOMPATIBLE** Renamed the ``asphalt.core.util`` module to ``asphalt.core.utils``
- The ``asphalt.core.event`` module was overhauled:

  - **BACKWARD INCOMPATIBLE** Removed the ``monotime`` attribute from the ``Event`` class
  - **BACKWARD INCOMPATIBLE** Dropped the ``return_future`` argument from ``Signal.dispatch()``
    and ``Signal.dispatch_event()`` – they now always return an awaitable that resolves to a
    boolean, indicating whether all callbacks were successful or not
  - **BACKWARD INCOMPATIBLE** Made the ``max_queue_size`` argument in ``Signal.stream_events`` and
    ``stream_events()`` into a keyword-only argument
  - **BACKWARD INCOMPATIBLE** ``Signal.dispatch_event()`` was renamed to ``Signal.dispatch_raw()``
  - Added the ``filter`` argument to ``Signal.stream_events()`` and ``stream_events()`` which can
    restrict the events that are yielded by them
  - Added the ``time`` constructor argument to the ``Event`` class
- The ``asphalt.core.context`` module was overhauled:

  - "lazy resources" are now called "resource factories"
  - ``Context.get_resources()`` now returns a set of ``ResourceContainer`` (instead of a list)
  - **BACKWARD INCOMPATIBLE** The ``default_timeout`` parameter was removed from the ``Context``
    constructor
  - **BACKWARD INCOMPATIBLE** The ``timeout`` parameter of ``Context.request_resource()`` was
    removed
  - **BACKWARD INCOMPATIBLE** The ``alias`` parameter of ``Context.request_resource()`` was
    renamed to ``name``
  - **BACKWARD INCOMPATIBLE** Removed the ``Context.finished`` signal in favor of the new
    ``add_teardown_callback()`` method which has different semantics (callbacks are called in LIFO
    order and awaited for one at a time)
  - **BACKWARD INCOMPATIBLE** Removed the ability to remove resources from a ``Context``
  - Added several new methods to the ``Context`` class: ``close()``, ``get_resource()``,
    ``require_resource()``
  - **BACKWARD INCOMPATIBLE** ``Context.publish_resource()`` was renamed to
    ``Context.add_resource()``
  - **BACKWARD INCOMPATIBLE** ``Context.publish_lazy_resource()`` was renamed to
    ``Context.add_resource_factory()``
  - **BACKWARD INCOMPATIBLE** The ``Context.get_resources()`` method was removed until
    it can be replaced with a better thought out API
  - **BACKWARD INCOMPATIBLE** The ``Resource`` class was removed from the public API
  - Three new methods were added to the ``Context`` class to bridge ``asyncio_extras`` and
    ``Executor`` resources: ``call_async()``, ``call_in_executor()`` and ``threadpool()``
  - Added a new decorator, ``@executor`` to help run code in specific ``Executor`` resources
- The application runner (``asphalt.core.runner``) got some changes too:

  - **BACKWARD INCOMPATIBLE** The runner no longer cancels all active tasks on exit
  - **BACKWARD INCOMPATIBLE** There is now a (configurable, defaults to 5 seconds) timeout for
    waiting for the root component to start
  - Asynchronous generators are now closed after the context has been closed (on Python 3.6+)
  - The SIGTERM signal now cleanly shuts down the application
- Switched from ``asyncio_extras`` to ``async_generator`` as the async generator compatibility
  library
- Made the current event loop accessible (from any thread) as the ``loop`` property from any
  ``asphalt.core.context.Context`` instance to make it easier to schedule execution of async code
  from worker threads
- The ``asphalt.core.utils.merge_config()`` function now accepts ``None`` as either argument
  (or both)

**2.1.1** (2017-02-01)

- Fixed memory leak which prevented objects containing Signals from being garbage collected
- Log a message on startup that indicates whether optimizations (``-O`` or ``PYTHONOPTIMIZE``) are
  enabled

**2.1.0** (2016-09-26)

- Added the possibility to specify more than one configuration file on the command line
- Added the possibility to use the command line interface via ``python -m asphalt ...``
- Added the ``CLIApplicationComponent`` class to facilitate the creation of Asphalt based command
  line tools
- Root component construction is now done after installing any alternate event loop policy provider
- Switched YAML library from PyYAML to ruamel.yaml
- Fixed a corner case where in ``wait_event()`` the future's result would be set twice, causing an
  exception in the listener
- Fixed coroutine-based lazy resource returning a CoroWrapper instead of a Future when asyncio's
  debug mode has been enabled
- Fixed a bug where a lazy resource would not be created separately for a context if a parent
  context contained an instance of the same resource

**2.0.0** (2016-05-09)

- **BACKWARD INCOMPATIBLE** Dropped Python 3.4 support in order to make the code fully rely on the
  new ``async``/``await``, ``async for`` and ``async with`` language additions
- **BACKWARD INCOMPATIBLE** De-emphasized the ability to implicitly run code in worker threads.
  As such, Asphalt components are no longer required to transparently work outside of the event
  loop thread. Instead, use ``asyncio_extras.threads.call_async()`` to call asynchronous code from
  worker threads if absolutely necessary. As a direct consequence of this policy shift, the
  ``asphalt.core.concurrency`` module was dropped in favor of the ``asyncio_extras`` library.
- **BACKWARD INCOMPATIBLE** The event system was completely rewritten:

  - instead of inheriting from ``EventSource``, event source classes now simply assign ``Signal``
    instances to attributes and use ``object.signalname.connect()`` to listen to events
  - all event listeners are now called independently of each other and coroutine listeners are run
    concurrently
  - added the ability to stream events
  - added the ability to wait for a single event to be dispatched
- **BACKWARD INCOMPATIBLE** Removed the ``asphalt.command`` module from the public API
- **BACKWARD INCOMPATIBLE** Removed the ``asphalt quickstart`` command
- **BACKWARD INCOMPATIBLE** Removed the ``asphalt.core.connectors`` module
- **BACKWARD INCOMPATIBLE** Removed the ``optional`` argument of ``Context.request_resource()``
- **BACKWARD INCOMPATIBLE** Removed the ``asphalt.core.runners`` entry point namespace
- **BACKWARD INCOMPATIBLE** ``Component.start()`` is now required to be a coroutine method
- **BACKWARD INCOMPATIBLE** Removed regular context manager support from the ``Context`` class
  (asynchronous context manager support still remains)
- **BACKWARD INCOMPATIBLE** The ``Context.publish_resource()``,
  ``Context.publish_lazy_resource()`` and ``Context.remove_resource()`` methods are no longer
  coroutine methods
- **BACKWARD INCOMPATIBLE** Restricted resource names to alphanumeric characters and underscores
- Added the possibility to specify a custom event loop policy
- Added support for `uvloop <https://github.com/MagicStack/uvloop>`_
- Added support for `aiogevent <https://bitbucket.org/haypo/aiogevent>`_
- Added the ability to use coroutine functions as lazy resource creators (though that just makes
  them return a ``Future`` instead)
- Added the ability to get a list of all the resources in a Context
- Changed the ``asphalt.core.util.resolve_reference()`` function to return invalid reference
  strings as-is
- Switched from argparse to click for the command line interface
- All of Asphalt core's public API is now importable directly from ``asphalt.core``

**1.2.0** (2016-01-02)

- Moved the ``@asynchronous`` and ``@blocking`` decorators to the ``asphalt.core.concurrency``
  package along with related code (they're still importable from ``asphalt.core.util`` until v2.0)
- Added typeguard checks to fail early if arguments of wrong types are passed to functions

**1.1.0** (2015-11-19)

- Decorated ``ContainerComponent.start`` with ``@asynchronous`` so that it can be called by a
  blocking subclass implementation
- Added the ``stop_event_loop`` function to enable blocking callables to shut down Asphalt's event
  loop

**1.0.0** (2015-10-18)

- Initial release
