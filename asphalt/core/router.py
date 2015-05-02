from asyncio import iscoroutinefunction
from inspect import getfullargspec, isgeneratorfunction
from itertools import chain
from typing import Callable, Any

from .util import qualified_name, wrap_blocking_callable

__all__ = 'RoutingError', 'Endpoint', 'BaseRouter'


class RoutingError(LookupError):
    """Raised when no matching endpoint can be found for the given path."""

    def __init__(self, error: str, path: str):
        super().__init__(error, path)
        self.error = error
        self.path = path

    def __str__(self):
        return self.error


class Endpoint:
    __slots__ = 'func', 'blocking'

    def __init__(self, func: Callable[..., Any], blocking: bool=None):
        argspec = getfullargspec(func)
        if len(argspec.args) == 0 and not argspec.varargs:
            raise TypeError('{} cannot accept the context argument. '
                            'Please add one positional parameter in its definition.'.
                            format(qualified_name(func)))

        if iscoroutinefunction(func) or blocking is False:
            self.blocking = False
        else:
            if isgeneratorfunction(func):
                raise TypeError('{} is a generator but not a coroutine. '
                                'Either mark it as a coroutine or remove the yield statements.'.
                                format(qualified_name(func)))

            # Wrap the target callable to be run in a thread pool
            func = wrap_blocking_callable(func)
            self.blocking = True

        self.func = func


class BaseRouter:
    """Provides a base implementation for routers."""

    __slots__ = 'parent', 'path', 'routers', 'endpoints'

    path_separator = '.'  # separator for path elements

    def __init__(self, path: str=None):
        super().__init__()
        self.parent = None  # type: BaseRouter
        self.path = path
        self.routers = []  # type: List[BaseRouter]
        self.endpoints = []  # type: List[Endpoint]

    def add_router(self, router: 'BaseRouter', path: str=None):
        """
        Adds a sub-router to the hierarchy.

        :param router: the router to add
        :param path: a value for the implementation specific path scheme
        :raises TypeError: if the given router is incompatible with this one
        """

        if path is not None:
            router.path = path
        elif router.path is None:
            raise ValueError('path not specified and the given router does not have a path '
                             'defined')

        self.routers.append(router)
        router.parent = self

    @property
    def full_path(self):
        if self.path is None:
            raise ValueError('router has no path defined')

        path = self.path
        router = self.parent
        while router and router.path:
            path = router.path + router.path_separator + path
            router = router.parent

        return path

    @property
    def all_endpoints(self):
        return tuple(self.endpoints) + tuple(chain.from_iterable(
            router.all_endpoints for router in self.routers))

    @property
    def all_routers(self):
        return (self,) + tuple(chain.from_iterable(router.all_routers for router in self.routers))

    def __repr__(self):
        return '{}(path={!r})'.format(self.__class__.__name__, self.path)
