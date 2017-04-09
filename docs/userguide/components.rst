Working with components
=======================

Components are the basic building blocks of an Asphalt application. They have a narrowly defined
set of responsibilities:

#. Take in configuration through the constructor
#. Validate the configuration
#. Add resources to the context (in :meth:`~asphalt.core.component.Component.start`)
#. Close/shut down/clean up resources when the context is torn down (by directly adding a callback
   on the context with :meth:`~asphalt.core.context.Context.add_teardown_callback`, or by using
   :func:`~asphalt.core.context.context_teardown`)

The :meth:`~asphalt.core.component.Component.start` method is called either by the parent component
or the application runner with a :class:`~asphalt.core.context.Context` as its only argument.
The component can use the context to add resources for other components and the application
business logic to use. It can also request resources provided by other components to provide some
complex service that builds on those resources.

The :meth:`~asphalt.core.component.Component.start` method of a component is only called once,
during application startup. When all components have been started, they are disposed of.
If any of the components raises an exception, the application startup process fails and any context
teardown callbacks scheduled so far are called before the process is exited.

In order to speed up the startup process and to prevent any deadlocks, components should try to
add any resources as soon as possible before requesting any. If two or more components end up
waiting on each others' resources, the application will fail to start.
Also, if a component needs to perform lengthy operations like connection validation on network
clients, it should add all its resources first to avoid the application start timing out.

There is no rule stating that a component cannot add itself to the context as a resource.
The reason official Asphalt libraries do not usually do this is that most of them have the option
of providing multiple instances of their services, which is obviously not possible when you only
add the component itself as a resource.

.. hint::
    It is a good idea to use `type hints`_ with typeguard_ checks
    (``assert check_argument_types()``) in the component's ``__init__`` method to ensure that the
    received configuration values are of the expected type, but this is of course not required.

.. _type hints: https://www.python.org/dev/peps/pep-0484/
.. _typeguard: https://pypi.python.org/pypi/typeguard

Container components
--------------------

A *container component* is component that can contain other Asphalt components.
The root component of virtually any nontrivial Asphalt application is a container component.
Container components can of course contain other container components and so on.

When the container component starts its child components, each
:meth:`~asphalt.core.component.Component.start` call is launched in its own task. Therefore all the
child components start concurrently and cannot rely on the start order. This is by design.
The only way components should be relying on each other is by the sharing of resources in the
context.
