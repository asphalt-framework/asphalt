import inspect
from asyncio import get_event_loop
from concurrent.futures import Executor
from functools import wraps, partial
from typing import Callable, Union, TypeVar, Awaitable

from typeguard import check_argument_types

from asphalt.core import Context

__all__ = ('executor',)

T_Retval = TypeVar('T_Retval')


def executor(func_or_executor: Union[Executor, str, Callable[..., T_Retval]], *,
             _executor: Union[Executor, str] = None) -> Callable[..., Awaitable[T_Retval]]:
    """
    Decorate a function to run in an executor.

    If no executor (or ``None``) is given, the current event loop's default executor is used.
    Otherwise, the argument must be a PEP 3148 compliant thread pool executor or the name of an
    :class:`~concurrent.futures.Executor` instance.

    If a decorated callable is called in a worker thread, the executor argument is ignored and the
    wrapped function is called directly.

    Callables wrapped with this decorator must be used with ``await`` when called in the event loop
    thread.

    Example use with the default executor (``None``)::

        @threadpool
        def this_runs_in_threadpool(ctx):
           return do_something_cpu_intensive()

        async def request_handler(ctx):
            result = await this_runs_in_threadpool(ctx)

    With a named :class:`~concurrent.futures.Executor` resource::

        @threadpool('special_ops')
        def this_runs_in_threadpool(ctx):
           return do_something_cpu_intensive()

        async def request_handler(ctx):
            result = await this_runs_in_threadpool(ctx)

    :param func_or_executor: either a callable (when used as a decorator), an executor instance or
        the name of an :class:`~concurrent.futures.Executor` resource

    """
    def wrapper(*args, **kwargs):
        try:
            loop = get_event_loop()
        except RuntimeError:
            # Event loop not available -- we're in a worker thread
            return func_or_executor(*args, **kwargs)

        # Resolve the executor resource name to an Executor instance
        if isinstance(_executor, str):
            try:
                ctx = next(obj for obj in args[:2] if isinstance(obj, Context))
            except StopIteration:
                raise RuntimeError('the callable needs to be called with a Context as the '
                                   'first or second positional argument')

            executor = ctx.require_resource(Executor, _executor)
        else:
            executor = _executor

        callback = partial(func_or_executor, *args, **kwargs)
        return loop.run_in_executor(executor, callback)

    if not callable(func_or_executor):
        return partial(executor, _executor=func_or_executor)

    assert check_argument_types()
    assert not inspect.iscoroutinefunction(func_or_executor), \
        'Cannot wrap coroutine functions to be run in an executor'
    return wraps(func_or_executor)(wrapper)
