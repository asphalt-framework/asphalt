Working with components
=======================

.. py:currentmodule:: asphalt.core

Components are the basic building blocks of an Asphalt application. They have a narrowly
defined set of responsibilities:

#. Take in configuration through the initializer
#. Validate the configuration
#. Add resources to the context (in either :meth:`Component.prepare`,
   :meth:`Component.start` or both)
#. Close/shut down/clean up resources when the context is torn down (by directly adding
   a callback on the context with :meth:`Context.add_teardown_callback`, or by using
   :func:`context_teardown`)

Any Asphalt component can have *child components* added to it. Child components can
either provide resources required by the parent component, or extend the parent
component's functionality in some way.

For example, a web application component typically has child components provide
functionality like database access, job queues, and/or integrations with third party
services. Likewise, child components might also extend the web application by adding
new routes to it.

Component startup
-----------------

To start a component, be it a solitary component or the root component of a hierarchy,
call :func:`start_component` from within an active :class:`Context` and pass it the
component class as the first positional argument, and its configuration options as the
second argument.

.. warning:: **NEVER** start components by directly calling :meth:`Component.start`!
    While this may work for simple components, more complex components may fail to start
    as their child components are not started, nor is the :meth:`Component.prepare`
    method never called this way.

The sequence of events when a component is started by :func:`start_component`, goes as
follows:

#. The entire hierarchy of components is instantiated using the combination of
   hard-coded defaults (as passed to :meth:`Component.add_component`) and any
   configuration overrides
#. The component's :meth:`~Component.prepare` method is called
#. All child components of this component are started concurrently (starting from the
   :meth:`~Component.prepare` step)
#. The component's :meth:`~Component.start` method is called

For example, let's say you have the following components:

.. literalinclude:: snippets/components1.py

You should see the following lines in the output:

.. code-block:: text

    ParentComponent.prepare()
    ChildComponent.prepare() [child1]
    ChildComponent.start() [child1]
    ChildComponent.prepare() [child2]
    ChildComponent.start() [child2]
    ParentComponent.start()
    Hello, world from child1!
    Hello, world from child2!

As you can see from the output, the parent component's :meth:`~Component.prepare` method
is called first. Then, the child components are started, and their
:meth:`~Component.prepare` methods are called first, then :meth:`~Component.start`.
When all the child components have been started, only then is the parent component
started.

The parent component can only use resources from the child components in its
:meth:`~Component.start` method, as only then have the child components that provide
those resources been started. Conversely, the child components cannot depend on
resources added by the parent in its :meth:`~Component.start` method, as this method is
only run after the child components have already been started.

As ``child1`` and ``child2`` are started concurrently, they are able to use
:func:`get_resource` to request resources from each other. Just make sure they don't get
deadlocked by depending on resources provided by each other at the same time, in which
case both would be stuck waiting forever.

As a recap, here is what components can do with resources relative to their parent,
sibling and child components:

* Initializer (``__init__()``):

    * ✅ Can add child components
    * ❌ Cannot acquire resources
    * ❌ Cannot provide resources

* :meth:`Component.prepare`:

    * ❌ Cannot add child components
    * ✅ Can acquire resources provided by parent components in their
      :meth:`~Component.prepare` methods
    * ❌ Cannot acquire resources provided by parent components in their
      :meth:`~Component.start` methods
    * ✅ Can acquire resources provided by sibling components (but you must use
      :func:`get_resource` to avoid race conditions)
    * ❌ Cannot acquire resources provided by child components
    * ✅ Can provide resources to child components

* :meth:`Component.start`:

    * ❌ Cannot add child components
    * ✅ Can acquire resources provided by parent components in their
      :meth:`~Component.prepare` methods
    * ❌ Cannot acquire resources provided by parent components in their
      :meth:`~Component.start` methods
    * ✅ Can acquire resources provided by sibling components (but you must use
      :func:`get_resource` to avoid race conditions)
    * ✅ Can acquire resources provided by child components
    * ❌ Cannot provide resources to child components
