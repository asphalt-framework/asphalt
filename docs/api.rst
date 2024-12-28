API reference
=============

.. py:currentmodule:: asphalt.core

Components
----------

.. autoclass:: Component
.. autoclass:: CLIApplicationComponent
.. autofunction:: start_component

Concurrency
-----------

.. autofunction:: start_background_task_factory
.. autofunction:: start_service_task
.. autoclass:: TaskFactory
.. autoclass:: TaskHandle

Contexts and resources
----------------------

.. autoclass:: Context
.. autoclass:: ResourceEvent
.. autofunction:: current_context
.. autofunction:: context_teardown
.. autofunction:: add_resource
.. autofunction:: add_resource_factory
.. autofunction:: add_teardown_callback
.. autofunction:: get_resource
.. autofunction:: get_resource_nowait
.. autofunction:: inject
.. autofunction:: resource
.. autoexception:: AsyncResourceError
.. autoexception:: NoCurrentContext
.. autoexception:: ResourceConflict
.. autoexception:: ResourceNotFound

Events
------

.. autoclass:: Event
.. autoclass:: Signal
.. autoclass:: SignalQueueFull
.. autofunction:: stream_events
.. autofunction:: wait_event
.. autoexception:: UnboundSignal

Exceptions
----------

.. autoexception:: ComponentStartError

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
