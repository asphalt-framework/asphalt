Application architecture
========================

.. py:currentmodule:: asphalt.core

Asphalt applications are centered around the following building blocks:

* components
* contexts
* resources
* signals/events
* the application runner

*Components* (:class:`Component`) are classes that initialize one or more
services, like network servers or database connections and add them to the *context* as
*resources*. Components are started by the application runner and usually discarded
afterwards.

*Contexts* (:class:`Context`) are "hubs" through which *resources* are shared
between components. Contexts can be chained by setting a parent context for a new
context. A context has access to all its parents' resources but parent contexts cannot
access the resources of their children.

*Resources* are any arbitrary objects shared through a context. Every resource is shared
on a context using its type (class) and name (chosen by the component). Every
combination of type/name is unique in a context.

*Resource factories* are callables used to provide a resource of certain type to a
context *on demand*. These are used, for example, to provide a database session or
transaction, where sharing a single resource across multiple tasks would be undesirable,
and you want to bind the life cycle of the resource to the context where the resource is
created.

*Signals* are the standard way in Asphalt applications to send events to interested
parties. Events are dispatched asynchronously without blocking the sender. The signal
system was loosely modeled after the signal system in the Qt_ toolkit.

The *application runner* (:func:`~run_application`) is a function that is used
to start an Asphalt application. It configures the Python logging module, creates the
root context, sets up any signal handlers, starts the root component and then runs the
event loop until the application exits. A command line tool (``asphalt``) is provided to
better facilitate the running of Asphalt applications. This tool reads the application
configuration from one or more YAML_ formatted configuration files and calls
:func:`run_application` with the resulting configuration dictionary as keyword
arguments. The settings from the configuration file are merged with hard coded defaults
so the config file only needs to override settings where necessary.

The following chapters describe in detail how each of these building blocks work.

.. _Qt: https://www.qt.io/
.. _YAML: http://yaml.org/
