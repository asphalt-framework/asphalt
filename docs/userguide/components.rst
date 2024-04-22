Working with components
=======================

.. py:currentmodule:: asphalt.core

Components are the basic building blocks of an Asphalt application. They have a narrowly
defined set of responsibilities:

#. Take in configuration through the constructor
#. Validate the configuration
#. Add resources to the context (in :meth:`Component.start`)
#. Close/shut down/clean up resources when the context is torn down (by directly adding
   a callback on the context with :meth:`Context.add_teardown_callback`, or by using
   :func:`context_teardown`)

The :meth:`Component.start` method is called either by the parent component or the
application runner (:func:`run_application`). The component can use the context to add
resources for other components and the application business logic to use. It can also
request resources provided by other components to provide some complex service that
builds on those resources.

The :meth:`Component.start` method of a component is only called once, during
application startup. When all components have been started, they are disposed of. If any
of the components raises an exception, the application startup process fails and any
context teardown callbacks scheduled so far are called before the process is exited.

In order to speed up the startup process and to prevent any deadlocks, components should
try to add any resources as soon as possible before requesting any. If two or more
components end up waiting on each others' resources, the application will fail to start.

Container components
--------------------

A *container component* is component that can contain other Asphalt components.
The root component of virtually any nontrivial Asphalt application is a container
component. Container components can of course contain other container components and so
on.

When the container component starts its child components, each :meth:`Component.start`
call is launched in its own task. Therefore all the child components start concurrently
and cannot rely on the start order. This is by design. The only way components should be
relying on each other is by the sharing of resources in the context. If a component
needs a resource from its "sibling" component, it should pass the ``wait=True`` option
to :func:`get_resource` in order to block until that resource becomes available. Note,
however, that if that resource is never added by any component in the context, the
application start-up will time out.
