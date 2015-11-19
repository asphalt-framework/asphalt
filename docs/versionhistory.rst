Version history
===============

1.1.0
-----

- Decorated :meth:`asphalt.core.component.ContainerComponent.start` with ``@asynchronous`` so that
  it can be called by a blocking subclass implementation
- Added the :func:`asphalt.core.util.stop_event_loop` function to enable blocking callables to
  shut down Asphalt's event loop

1.0.0
-----

Initial release.
