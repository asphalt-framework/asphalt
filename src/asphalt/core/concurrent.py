from __future__ import annotations

__all__ = ("executor",)

import inspect
import sys
from asyncio import get_running_loop
from concurrent.futures import Executor
from functools import partial, wraps
from typing import Awaitable, Callable, TypeVar, overload

from asphalt.core import Context

if sys.version_info >= (3, 10):
    from typing import Concatenate, ParamSpec
else:
    from typing_extensions import Concatenate, ParamSpec

T_Retval = TypeVar("T_Retval")
P = ParamSpec("P")


@overload
def executor(
    func_or_executor: Executor | str,
) -> Callable[
    [Callable[Concatenate[Context, P], T_Retval]],
    Callable[Concatenate[Context, P], T_Retval | Awaitable[T_Retval]],
]:
    ...


@overload
def executor(
    func_or_executor: Callable[Concatenate[Context, P], T_Retval]
) -> Callable[Concatenate[Context, P], T_Retval | Awaitable[T_Retval]]:
    ...


def executor(
    func_or_executor: Executor | str | Callable[Concatenate[Context, P], T_Retval]
) -> (
    Callable[
        [Callable[Concatenate[Context, P], T_Retval]],
        Callable[Concatenate[Context, P], T_Retval | Awaitable[T_Retval]],
    ]
    | Callable[Concatenate[Context, P], T_Retval | Awaitable[T_Retval]]
):
    """
    Decorate a function to run in an executor.

    If no executor (or ``None``) is given, the current event loop's default executor is
    used. Otherwise, the argument must be a PEP 3148 compliant thread pool executor or
    the name of an :class:`~concurrent.futures.Executor` instance.

    If a decorated callable is called in a worker thread, the executor argument is
    ignored and the wrapped function is called directly.

    Callables wrapped with this decorator must be used with ``await`` when called in the
    event loop thread.

    Example use with the default executor (``None``)::

        @executor
        def this_runs_in_threadpool(ctx):
           return do_something_cpu_intensive()

        async def request_handler(ctx):
            result = await this_runs_in_threadpool(ctx)

    With a named :class:`~concurrent.futures.Executor` resource::

        @executor('special_ops')
        def this_runs_in_threadpool(ctx):
           return do_something_cpu_intensive()

        async def request_handler(ctx):
            result = await this_runs_in_threadpool(ctx)

    :param func_or_executor: either a callable (when used as a decorator), an executor
        instance or the name of an :class:`~concurrent.futures.Executor` resource

    """

    def outer(
        func: Callable[Concatenate[Context, P], T_Retval]
    ) -> Callable[Concatenate[Context, P], T_Retval | Awaitable[T_Retval]]:
        def wrapper(
            ctx: Context, *args: P.args, **kwargs: P.kwargs
        ) -> T_Retval | Awaitable[T_Retval]:
            try:
                loop = get_running_loop()
            except RuntimeError:
                # Event loop not available -- we're in a worker thread
                return func(ctx, *args, **kwargs)

            # Resolve the executor resource name to an Executor instance
            _executor: Executor | None
            if isinstance(executor, str):
                _executor = ctx.require_resource(Executor, executor)
            else:
                _executor = executor

            callback = partial(func, ctx, *args, **kwargs)
            return loop.run_in_executor(_executor, callback)

        assert not inspect.iscoroutinefunction(
            func
        ), "Cannot wrap coroutine functions to be run in an executor"
        return wraps(func)(wrapper)

    executor: Executor | str | None = None
    if isinstance(func_or_executor, (str, Executor)):
        executor = func_or_executor
        return outer
    else:
        return outer(func_or_executor)
