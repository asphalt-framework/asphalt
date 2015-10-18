Events
======

Events are a handy way to make your code react to changes in another part of the application.
Asphalt events originate from classes that inherit the :class:`~asphalt.core.context.EventSource`
mixin class. :class:`~asphalt.core.context.Context` is an example of such a class.

To listen to events dispatched from an :class:`~asphalt.core.context.EventSource`, call
:meth:`~asphalt.core.context.EventSource.add_listener` to add an event listener callback:

.. code-block:: python

    from asphalt.core.event import Event
    from asphalt.core.context import Context


    def listener(event: Event):
        print('The context is finished!')

    context = Context()
    context.add_listener('finished')
    context.dispatch('finished')

Creating new event sources
--------------------------

TODO
