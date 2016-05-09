Version history
===============

This library adheres to `Semantic Versioning <http://semver.org/>`_.

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
