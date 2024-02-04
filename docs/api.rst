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

.. autofunction:: start_background_task
.. autofunction:: start_service_task

Contexts and resources
----------------------

.. autoclass:: Context
.. autoclass:: ResourceEvent
.. autofunction:: current_context
.. autofunction:: context_teardown
.. autofunction:: add_resource
.. autofunction:: get_resource
.. autofunction:: require_resource
.. autofunction:: inject
.. autofunction:: resource
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
