Version history
===============

This library adheres to `Semantic Versioning <http://semver.org/>`_.

**2.0.0**

- **BACKWARD INCOMPATIBLE** Dropped Python 3.4 support in order to make the code fully rely on the
  new ``async``/``await``, ``async for`` and ``async with`` language additions
- **BACKWARD INCOMPATIBLE** Dropped the ``asphalt.core.concurrency`` module in favor of the
  ``asyncio_extras`` library
- **BACKWARD INCOMPATIBLE** De-emphasized the ability to run code in worker threads.
  As such, Asphalt components are no longer required to transparently work outside of the event
  loop thread. Instead, use ``asyncio_extras.threads.call_async()`` to call asynchronous code if
  absolutely necessary.
- **BACKWARD INCOMPATIBLE** The ``Component.start()`` method is now required to be a coroutine
- **BACKWARD INCOMPATIBLE** Removed the ``asphalt.command`` module from the public API
- **BACKWARD INCOMPATIBLE** Removed the ``asphalt quickstart`` command
- **BACKWARD INCOMPATIBLE** Removed regular context manager support from the ``Context`` class
  (asynchronous context manager support still remains)
- **BACKWARD INCOMPATIBLE** Restricted resource names to alphanumeric characters and underscores
- **BACKWARD INCOMPATIBLE** Modified event dispatch logic in ``EventSource`` to always run all
  event listeners even if some listeners raise exceptions. A uniform exception is then raised
  that contains all the exceptions and the listeners who raised them.
- **BACKWARD INCOMPATIBLE** Renamed the ``EventSource.dispatch()`` method to ``dispatch_event``
  to disambiguate the operation and to prevent name clashes with subclasses
- **BACKWARD INCOMPATIBLE** Event topic registrations for ``EventSource`` subclasses are now done
  using the ``@register_topic`` class decorator instead of the ``_register_topic()`` method
- **BACKWARD INCOMPATIBLE** Removed the ``asphalt.core.connectors`` module
- **BACKWARD INCOMPATIBLE** Removed the ``optional`` argument of ``Context.request_resource()``
- **BACKWARD INCOMPATIBLE** Removed the ``asphalt.core.runners`` entry point namespace
- Added the possibility to specify a custom event loop policy
- Added support for `uvloop <https://github.com/MagicStack/uvloop>`
- Added the ability to use coroutine functions as lazy resource creators (though that just makes
  them return a ``Future`` instead)
- Added the ability to get a list of all the resources in a Context
- Added the ability to listen to multiple topics in an EventSource with a single listener
- Added the ability to stream events from an EventSource
- Added a utility function to listen to a single event coming from an EventSource
- Changed the ``asphalt.core.util.resolve_reference()`` function to return invalid reference
  strings as-is
- Switched from argparse to click for the command line interface
- All classes and functions are now importable directly from ``asphalt.core``

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
