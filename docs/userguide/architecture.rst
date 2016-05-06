Application architecture
========================

Asphalt applications are built by assembling a hierarchy of *components*. Each component typically
provides some specific functionality for the application, like a network server or client, a
database connection or a myriad of other things. A component's lifecycle is usually very short:
it's instantiated and its :meth:`~asphalt.core.component.Component.start` method is run and the
component is then discarded.

Components work together through a shared :class:`~asphalt.core.context.Context`. A context is
essentially a container for *resources*. Resources can be any arbitrary objects like data or
services (such as database connections).

Contexts can have subcontexts. How and if subcontexts are used dependes on the components using
them. For example, a component serving network requests will want to create a subcontext for each
request it handles to store request specific information and other state. While the subcontext will
have its own independent state, it also has full access the resources of its parent context.

An Asphalt application is normally started by the *runner*. The runner is a function that
initializes the logging system, the event loop, the top level context and then starts the root
component. The root component will start any subcomponents and so on. Finally, the runner will just
keep the event loop running until the application is shut down.

The runner is further extended by the ``asphalt`` command line tool which can read a YAML formatted
configuration file and pass the parsed configuration to the runner. This allows applications to
have different development and production configurations without having to modify any actual code.

Components
==========

A component class is the basic building block of an Asphalt application. It has a very narrowly
defined set of responsibilities:

#. Inherit from :class:`~asphalt.core.component.Component` or one of its subclasses
#. In ``__init__()``, validate the configuration
#. In :meth:`~asphalt.core.component.Component.start`, request and publish resources as necessary
#. When the context (given as an argument to :meth:`~asphalt.core.component.Component.start`)
   finishes, clean up any resources (close files and network connections, terminate subprocesses
   etc.)

The component class typically receives its configuration through its constructor. Typically, the
configuration values should ideally be accompanied by argument type annotations and typeguard_
checks (``assert check_argument_types()``) to ensure that the received configuration values are of
the expected type. These checks are not mandatory, but they are strongly recommended to make
troubleshooting easier.

In the :meth:`~asphalt.core.component.Component.start` method, the component receives a *context*
as its only argument. The context is used by components to provide *resources* to each other and
any other parts of the application, such as request handlers in a network server like application.
It is also used to store any "contextual" data (such as request/response objects in aforementioned
server-like applications).

The :meth:`~asphalt.core.component.Component.start` method of a component is only called once,
during application startup. If any of the components raises an exception, the application startup
process fails and the context is finished.

.. _typeguard: https://pypi.python.org/pypi/typeguard

Container components
--------------------

A *container component* is component that can contain other Asphalt components.
The root component of virtually any nontrivial Asphalt application is a container component.
Container components can of course contain other container components and so on.

When the container component starts its child components, each
:meth:`~asphalt.core.component.Component.start` call is launched in its own asyncio task. Therefore
all the child components start concurrently and cannot rely on the start order. This is by design.
The only way components should be relying on each other is by the publishing and requesting of
resources in their shared context.

Contexts and subcontexts
------------------------

A context is essentially a namespace for resources and data. The basic
:class:`~asphalt.core.context.Context` class doesn't contain any extra data. Subclasses,


Sometimes it is desirable to create *subcontexts* of a parent context.
Typical uses cases for that include:

* Request handlers for network services
* Components that support specialized subcomponents
* Complex reusable application add-ons (a reusable component with its own set of subcomponents)

Subcontexts have access to all the resources of the parent context, but parent contexts cannot
access resources from their subcontexts. It is also good to remember that a context does not
necessarily have to have a parent context. How, when and where you use such contexts is up to you.

Resources
---------

While any object can be a resource, the question is, what *should* be published as a resource in
the context? The answer boils down to what is *needed* by other components. Here are a few examples
of resources that might be useful to share among multiple components:

* Database connections
* Remote service handles
* SSL contexts

Another consideration is the *types* of the resources they're published as. One of the core ideas
of resource publishing is that you could just switch a resource for another of the same type and
not have to reconfigure the rest of the application. For example, think of a mailer. A mailer
has a certain API and the code that uses the mailer does not (should not) have to care about the
implementation of the API. So if you want to switch one mailer for another, you can do so without
breaking your application. But this requires that the mailer resource is published using a common
supertype. In this particular example, the mailer resources are published by asphalt-mailer_ as
the ``asphalt.mailer.api.Mailer`` type. It is a good idea to use an abstract class as the published
resource type when multiple implementations are expected in order to minimize the implementation
constraints.

It is a good idea to publish any resources as soon as possible before requesting any, to speed up
the startup process and to prevent any deadlocks (when two or more components wait on each others'
resources). Also, if you need to perform lengthy operations like connection validation on network
clients, it is strongly recommended to first publish them and any other resources other components
might be waiting on, to prevent occasional timeouts.

.. _asphalt-mailer: https://github.com/asphalt-framework/asphalt-mailer

Lazy resources
--------------

Resources can also be published *lazily*. That means they're created *on demand*, as part of the
context where they were requested. There are a couple reasons for publishing resources this way:

* Their setup is expensive
* They are context sensitive to be specific to the context in which they're used

Lazy resources are published using :meth`~asphalt.core.context.Context.publish_lazy_resource`.
Instead of giving a resource object to it, you give it a callable that takes the local context
object (whatever that may be) as the argument and returns the created resource object. The creator
callable will only be called once at most in each context.

The creator callable can also be a coroutine function or return an awaitable, in which case the
coroutine or other awaitable is resolved before returning the resource object to the caller. This
approach has the unfortunate limitation that the awaitable cannot be automatically resolved on
attribute access so something like ``await ctx.foo`` is required when such resources are used
through their context attributes.
