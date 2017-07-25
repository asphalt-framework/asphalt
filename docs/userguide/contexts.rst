Working with contexts and resources
===================================

Every Asphalt application has at least one context: the root context. The root context is typically
created by the :func:`~asphalt.core.runner.run_application` function and passed to the root
component. This context will only be closed when the application is shutting down.

Most nontrivial applications will make use of *subcontexts*. A subcontext is a context that has a
parent context. A subcontext can make use of its parent's resources, but the parent cannot access
the resources of its children. This enables developers to create complex services that work
together without risking interfering with each other.

Subcontexts can be roughly divided into two types: long lived and short lived ones. Long lived
subcontexts are typically used in container components to isolate its resources from the rest of
the application. Short lived subcontexts, on the other hand, usually encompass some *unit of work*
(UOW). Examples of such UOWs are:

* handling of a request in a network service
* running a scheduled task
* running a test in a test suite

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
``asphalt.mailer.api.Mailer``. To facilitate this loose coupling of services, it adds all its
configure mailer services using the ``Mailer`` interface so that components that just need *some*
was to send email don't have to care what implementation was chosen in the configuration.

.. _asphalt-mailer: https://github.com/asphalt-framework/asphalt-mailer

Adding resources to a context
-----------------------------

Resources can be added to a context in two forms: regular resources and resource factories.
A regular resource can be any arbitrary object. The same object can be added to the context under
several different types, as long as the type/name combination remains unique within the same
context.

A resource factory is a callable that takes a :class:`~asphalt.core.context.Context` as an argument
an returns the value of the resource. There are at least a couple reasons to use resource factories
instead of regular resources:

  * the resource's lifecycle needs to be bound to the local context (example: database
    transactions)
  * the resource requires access to the local context (example: template renderers)

Getting resources from a context
--------------------------------

The :class:`~asphalt.core.context.Context` class offers a few ways to look up resources.

The first one, :meth:`~asphalt.core.context.Context.get_resource`, looks for a resource or resource
factory matching the given type and name. If the resource is found, it returns its value.

The second one, :meth:`~asphalt.core.context.Context.require_resource`, works exactly the same way
except that it raises :exc:`~asphalt.core.context.ResourceNotFound` if the resource is not found.

The third method, meth:`~asphalt.core.context.Context.request_resource`, calls
:meth:`~asphalt.core.context.Context.get_resource` and if the resource is not found, it waits
indefinitely for the resource to be added to the context or its parents. When that happens, it
calls :meth:`~asphalt.core.context.Context.get_resource` again, at which point success is
guaranteed. This is usually used only in the components'
:meth:`~asphalt.core.component.Component.start` methods.

The order of resource lookup is as follows:

#. search for a resource in the local context
#. search for a resource factory in the local context and its parents and, if found, generate the
   local resource
#. search for a resource in the parent contexts

Handling resource cleanup
-------------------------

Any code that adds resources to a context is also responsible for cleaning them up when the context
is closed. This usually involves closing sockets and files and freeing whatever system resources
were allocated. This should be done in a *teardown callback*, scheduled using
:meth:`~asphalt.core.context.Context.add_teardown_callback`. When the context is closed, teardown
callbacks are run in the reverse order in which they were added, and always one at a time, unlike
with the :class:`~asphalt.core.event.Signal` class. This ensures that a resource that is still in
use by another resource is never cleaned up prematurely.

For example::

    from asphalt.core import Component


    class FooComponent(Component):
        async def start(ctx):
            service = SomeService()
            await service.start(ctx)
            ctx.add_teardown_callback(service.stop)
            ctx.add_resource(service)


There also exists a convenience decorator, :func:`~asphalt.core.context.context_teardown`, which
makes use of asynchronous generators::

    from asphalt.core import Component, context_teardown
    from async_generator import yield_


    class FooComponent(Component):
        @context_teardown
        async def start(ctx):
            service = SomeService()
            await service.start(ctx)
            ctx.add_resource(service)

            await yield_()  # just "yield" on Python 3.6+

            # This part of the function is run when the context is closing
            service.stop()

Sometimes you may want the cleanup to know whether the context was ended because of an unhandled
exception. The one use that has come up so far is committing or rolling back a database
transaction. This can be achieved by passing the ``pass_exception`` keyword argument to
:meth:`~asphalt.core.context.Context.add_teardown_callback`::

    class FooComponent(Component):
        async def start(ctx):
            def teardown(exception: Optional[BaseException]):
                if exception:
                    db.rollback()
                else:
                    db.commit()

            db = SomeDatabase()
            await db.start(ctx)
            ctx.add_teardown_callback(teardown, pass_exception=True)
            ctx.add_resource(db)

The same can be achieved with :func:`~asphalt.core.context.context_teardown` by storing the yielded
value::

    class FooComponent(Component):
        @context_teardown
        async def start(ctx):
            db = SomeDatabase()
            await db.start(ctx)
            ctx.add_resource(db)

            exception = await yield_()

            if exception:
                db.rollback()
            else:
                db.commit()

If any of the teardown callbacks raises an exception, the cleanup process will still continue, but
at the end a :exc:`~asphalt.core.context.TeardownError` will be raised. This exception contains all
the raised exceptions in its ``exceptions`` attribute.
