Version history
===============

**2.0.0**

- *BACKWARD INCOMPATIBLE* Dropped Python 3.4 support in order to make the code fully rely on the
  new ``async``/``await``, ``async for`` and ``async with`` language additions
- *BACKWARD INCOMPATIBLE* Dropped the ``asphalt.core.concurrency`` module in favor of the
  ``asyncio_extras`` library
- *BACKWARD INCOMPATIBLE* De-emphasized the ability to run code in worker threads.
  As such, Asphalt components are no longer required to transparently work outside of the event
  loop thread. Instead, use``asyncio_extras.threads.call_async()`` to call asynchronous code if
  absolutely necessary.
- *BACKWARD INCOMPATIBLE* Removed regular context manager support from the ``Context`` class
  (asynchronous context manager support still remains)
- *BACKWARD INCOMPATIBLE* Modified event dispatch logic in ``EventSource`` to always run all
  event listeners even if some listeners raise exceptions. A uniform exception is then raised
  that contains all the exceptions and the listeners who raised them.
- Added the ability to listen to multiple topics in an EventSource with a single listener
- Added the ability to stream events from an EventSource

**1.3.0**

- Allow the context manager of the ``Context`` class to be used from a non-eventloop thread when
  the event loop is running

**1.2.0**

- Moved the ``@asynchronous`` and ``@blocking`` decorators to the ``asphalt.core.concurrency``
  package along with related code (they're still importable from ``asphalt.core.util`` until v2.0)
- Added typeguard checks to fail early if arguments of wrong types are passed to functions

**1.1.0**

- Decorated ``ContainerComponent.start`` with ``@asynchronous`` so that it can be called by a
  blocking subclass implementation
- Added the ``stop_event_loop`` function to enable blocking callables to shut down Asphalt's event
  loop

**1.0.0**

Initial release.
