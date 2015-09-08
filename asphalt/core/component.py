from abc import ABCMeta, abstractmethod

from .context import Context

__all__ = 'Component',


class Component(metaclass=ABCMeta):
    """This is the base class for all Asphalt components."""

    __slots__ = ()

    @abstractmethod
    def start(self, ctx: Context):
        """
        This method is called on application start. It can be a coroutine.

        The context can be used to:
          * add context event listeners (:meth:`~Context.add_event_listener`)
          * add resources (:meth:`~Context.add_resource`)
          * add lazily created resources (:meth:`~Context.add_lazy_resource`)
          * request resources (:meth:`~Context.request_resource`)

        If the component requests any resources, it is advisable to first add all the resources
        it can before requesting any. This will speed up the dependency resolution and prevent
        deadlocks.

        :param ctx: the context for which the component was created
        """
