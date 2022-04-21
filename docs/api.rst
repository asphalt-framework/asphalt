API reference
=============

.. py:currentmodule:: asphalt.core

Components
----------

.. autoclass:: Component
.. autoclass:: ContainerComponent
.. autoclass:: CLIApplicationComponent

Concurrency
-----------

.. autofunction:: executor

Contexts and resources
----------------------

.. autoclass:: Context
.. autoclass:: ResourceEvent
.. autofunction:: current_context
.. autofunction:: context_teardown
.. autofunction:: inject
.. autofunction:: resource
.. autoexception:: TeardownError
.. autoexception:: NoCurrentContext
.. autoexception:: ResourceConflict
.. autoexception:: ResourceNotFound

Events
------

.. autoclass:: ResourceEvent
.. autoclass:: Signal
.. autofunction:: stream_events
.. autofunction:: wait_event

Application runner
------------------

.. autofunction:: run_application

Utilities
---------

.. autoclass:: PluginContainer
.. autofunction:: callable_name
.. autofunction:: merge_config
.. autofunction:: qualified_name
.. autofunction:: resolve_reference
