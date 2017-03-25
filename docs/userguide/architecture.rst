Application architecture
========================

Asphalt applications are built by assembling a hierarchy of *components*. Each component typically
provides some specific functionality for the application, like a network server or client, a
database connection or a myriad of other things. A component's lifecycle is usually very short:
it's instantiated and its :meth:`~asphalt.core.component.Component.start` method is run and the
component is then discarded. A common exception to this are command line tools, where the root
component's ``start()`` call typically lasts for the entire run time of the tool.

Components work together through a shared :class:`~asphalt.core.context.Context`. Every application
has at least a top level context which is passed to the root component's
:meth:`~asphalt.core.component.Component.start` method. A context is essentially a container for
*resources* and a namespace for arbitrary data attributes. Resources can be objects of any type
like data or services.

Contexts can have subcontexts. How and if subcontexts are used depends on the components using
them. For example, a component serving network requests may want to create a subcontext for each
request it handles to store request specific information and other state. While the subcontext will
have its own independent state, it also has full access the resources of its parent context.

An Asphalt application is normally started by calling :func:`~asphalt.core.runner.run_application`
with the root component as the argument. This function takes care of logging and and starting the
root component in the event loop. The application will then run until Ctrl+C is pressed, the
process is terminated from outside or the application code stops the event loop.

The runner is further extended by the ``asphalt`` command line tool which reads the application
configuration from a YAML formatted configuration file, instantiates the root component and calls
:func:`~asphalt.core.runner.run_application`. The settings from the configuration file are merged
with hard coded defaults so the config file only needs to override settings where necessary.

Components
----------

Components are the basic building blocks of an Asphalt application. They have a narrowly defined
set of responsibilities:

#. Take in configuration through the constructor
#. Validate the configuration
#. Publish resources (in :meth:`~asphalt.core.component.Component.start`)
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
cleanup callbacks scheduled so far are called before the process is exited.

In order to speed up the startup process and to prevent any deadlocks, components should try to
add any resources as soon as possible before requesting any. If two or more components end up
waiting on each others' resources, the application will fail to start.
Also, if a component needs to perform lengthy operations like connection validation on network
clients, it should add all its resources first to avoid the application start timing out.

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
The only way components should be relying on each other is by the adding and requesting of
resources in their shared context.

Context hierarchies
-------------------

As mentioned previously, every application has at least one context. Component code and application
business logic can create new contexts at any time, and a new context can be linked to a parent
context to take advantage of its resources. Such *subcontexts* have access to all the resources of
the parent context, but parent contexts cannot access resources from their subcontexts. Sometimes
it may also be beneficial to create completely isolated contexts to ensure consistent behavior
when some reusable code is plugged in an application.

A common use case for creating subcontexts is when a network server handles an incoming request.
Such servers typically want to create a separate subcontext for each request, usually using
specialized subclass of :class:`~asphalt.core.context.Context`.

Resources
---------

The resource system in Asphalt exists for two principal reasons:

* To avoid having to duplicate configuration
* To enable sharing of pooled resources, like database connection pools

Here are a few examples of services that will likely benefit from resource sharing:

* Database connections
* Remote service handles
* Serializers
* Template renderers
* SSL contexts

When you add a resource, you should make sure that the resource is discoverable using any
abstract interface or base class that it implements. This is so that consumers of the service don't
have to care if you switch the implementation of another. For example, consider a mailer service,
provided by asphalt-mailer_. The library has an abstract base class for all mailers,
``asphalt.mailer.api.Mailer``. To facilitate this loose coupling of services, it adds all mailers
as Mailers.

.. _asphalt-mailer: https://github.com/asphalt-framework/asphalt-mailer

Resource factories
------------------

There are certain types of resources that should always be local to the context that they are
accessed from. To this end, it is possible to use *resource factories*. Instead of adding a
concrete object to a context as a resource, you instead call
:meth:`~asphalt.core.context.Context.add_resource_factory` and pass it a callable that takes a
context object as the argument and returns the actual resource object. The callback is called
whenever a resource matching the name and any of of the specified types of the resource factory is
being requested (and is not already present in the context), or its designated context attribute is
being accessed for the first time. Each context object always gets its very own resource object
from the factory, even if a parent context already has one.

There are at least a couple plausible reasons for using resource factories:

* The resource needs access to the resources or data specific to the local context
  (example: template renderers)
* The life cycle of the resource needs to be tied to the life cycle of the context
  (example: database transactions)
