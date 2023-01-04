from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from anyio.abc import TaskGroup

from ._context import Context, current_context, get_resource_nowait
from ._exceptions import ApplicationExit

logger = logging.getLogger(__name__)


def start_background_task(
    func: Callable[..., Coroutine[Any, Any, Any]], name: str
) -> None:
    """
    Start a task that runs independently on the background.

    The task runs in its own context, inherited from the root context.
    If the task raises an exception, the exception is logged with a descriptive message
    containing the task's name.

    To pass arguments to the target callable, pass them via lambda (e.g.
    ``lambda: yourfunc(arg1, arg2, kw=val)``)

    :param func: the coroutine function to run
    :param name: descriptive name for the task

    """

    async def run_background_task() -> None:
        logger.debug("Background task (%s) starting", name)
        try:
            async with Context():
                await func()
        except Exception:
            logger.exception("Background task (%s) crashed", name)
        else:
            logger.debug("Background task (%s) finished", name)

    ctx = current_context()
    while ctx.parent:
        ctx = ctx.parent

    root_taskgroup = get_resource_nowait(
        TaskGroup, "root_taskgroup"  # type: ignore[type-abstract]
    )
    root_taskgroup.start_soon(run_background_task, name=name)


def start_service_task(
    func: Callable[..., Coroutine[Any, Any, Any]], name: str
) -> None:
    """
    Start a task that runs independently on the background.

    The task runs in its own context, inherited from the root context.
    If the task raises an exception, it is propagated to the application runner,
    triggering the termination of the application.

    To pass arguments to the target callable, pass them via lambda (e.g.
    ``lambda: yourfunc(arg1, arg2, kw=val)``)

    :param func: the coroutine function to run
    :param name: descriptive name for the task

    """

    async def run_service_task() -> None:
        logger.debug("Service task (%s) starting", name)
        try:
            async with Context():
                await func()
        except ApplicationExit:
            logger.info(
                "Service task (%s) requested the application to be shut down", name
            )
            raise
        except SystemExit as exc:
            # asyncio stops the loop prematurely if a base exception like SystemExit
            # is raised, so we work around that with Asphalt's SoftSystemExit which
            # inherits from Exception instead
            raise ApplicationExit(exc.code).with_traceback(exc.__traceback__) from None
        except Exception:
            logger.exception(
                "Service task (%s) crashed – terminating application", name
            )
            raise
        else:
            logger.info("Service task (%s) finished", name)

    ctx = current_context()
    while ctx.parent:
        ctx = ctx.parent

    root_taskgroup = get_resource_nowait(
        TaskGroup, "root_taskgroup"  # type: ignore[type-abstract]
    )
    root_taskgroup.start_soon(run_service_task, name=name)
