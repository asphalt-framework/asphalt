Version history
===============

**2.0.0**

- *BACKWARD INCOMPATIBLE* Dropped Python 3.4 support in order to make the code fully rely on the
  new ``async``/``await``, ``async for`` and ``async with`` language additions
- *BACKWARD INCOMPATIBLE* De-emphasized the ability to run code in worker threads.
  It is now recommended to minimize the use of worker threads.
  As a result, the ``@asynchronous`` decorator has been removed.
- *BACKWARD INCOMPATIBLE* Replaced the ``@blocking`` decorator with the ``threadpool`` function
  that acts both as a decorator and an asynchronous context manager that executes wrapped code in
  a worker thread
- *BACKWARD INCOMPATIBLE* Removed the deprecated ``blocking`` and ``asynchronous`` aliases from the
  ``asphalt.core.util`` module
- *BACKWARD INCOMPATIBLE* Removed regular context manager support from the ``Context`` class
  (asynchronous context manager support still remains)
- Added an asynchronous wrapper for running file I/O operations in a worker thread
- Added wrappers for easily creating asynchronous generators and context managers


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
