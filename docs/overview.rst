Architectural overview
======================

TODO

Core concepts
*************

To better understand how Asphalt works and how it's used, it is recommended that you
familiarize yourself with the core concepts described below.


.. _context:

Context
-------

A :class:`~asphalt.core.context.Context` is a container for resources and any "contextual"
information and functionality. It would also be a good analogue to think of a context as a
"resource marketplace" where resources are offered and requested.

Every Asphalt application has at least one context because one is required in the startup phase of
every :class:`~asphalt.core.component.Component`.

Contexts can have subcontexts. All the resources and context variables are accessible from any
subcontext but parent contexts cannot access any resources or variables of their subcontexts.
A typical use for subcontexts is in request handlers of network services, where the handler needs
access to access to all the services


.. _resource:

Resource
--------

Resources (:class:`~asphalt.core.context.Resource`) are objects of any type, accompanied by
the following metadata:

* one or more *types*
* exactly one *name*
* optionally a *context variable name*.

A resource's name is always unique within each of its types in the context in which it was defined.

A resource can be *lazy* -- created only on demand (when it is requested or its context variable is
accessed). A lazy resource is always created for the context it was requested from and not the one
it was originally added to.


.. _component:

Component
---------

Components (:class:`~asphalt.core.component.Component`) are containers for reusable code.
They typically provide integration with some third party library or implement some network
protocol. Components integrate with each other by making available and requesting resources from
the context.

*Container components* can contain other components. They are typically used for two purposes:

* as the top level component of an application
* as the top level component of a reuseable subsystem (such as a manager subapplication)
