Version history
===============

This library adheres to `Semantic Versioning <http://semver.org/>`_.

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
