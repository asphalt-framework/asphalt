from abc import ABCMeta, abstractmethod

from .context import ApplicationContext

__all__ = 'Component',


class Component(metaclass=ABCMeta):
    """This is the base class for all Asphalt components."""

    __slots__ = ()

    @abstractmethod
    def start(self, app_ctx: ApplicationContext):
        """
        This method is called on application start. It can be a coroutine.

        The application context can be used to:
          * add application start/finish callbacks
          * add default callbacks for other contexts
          * add context getters
          * add resources (via app_ctx.resources)
          * request resources (via app_ctx.resources)

        When dealing with resources, it is advisable to add as many resources as possible
        before requesting any. This will speed up the dependency resolution.

        :param app_ctx: the application context
        """
