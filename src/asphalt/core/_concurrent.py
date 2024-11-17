from __future__ import annotations

import logging
import sys
from collections.abc import Coroutine
from dataclasses import dataclass, field
from inspect import Parameter, signature
from typing import Any, Callable, TypeVar

from anyio import (
    TASK_STATUS_IGNORED,
    CancelScope,
    Event,
    create_task_group,
)
from anyio.abc import TaskGroup, TaskStatus

from ._context import Context
from ._utils import callable_name

if sys.version_info >= (3, 10):
    from typing import TypeAlias
else:
    from typing_extensions import TypeAlias

T_Retval = TypeVar("T_Retval")
ExceptionHandler: TypeAlias = Callable[[Exception], bool]

logger = logging.getLogger("asphalt.core")


@dataclass(unsafe_hash=True)
class TaskHandle:
    """
    A representation of a task started from :class:`TaskFactory`.

    :ivar name: the name of the task
    :ivar start_value: the start value passed to ``task_status.started()`` if the target
        function supported that
    """

    name: str = field(hash=False, compare=False)
    start_value: Any = field(init=False, repr=False, compare=False, hash=False)
    _cancel_scope: CancelScope = field(
        init=False, default_factory=CancelScope, repr=False
    )
    _finished_event: Event = field(
        init=False, default_factory=Event, repr=False, compare=False
    )

    def cancel(self) -> None:
        """Schedule the task to be cancelled."""
        self._cancel_scope.cancel()

    async def wait_finished(self) -> None:
        """Wait until the task is finished."""
        await self._finished_event.wait()


async def run_background_task(
    func: Callable[..., Coroutine[Any, Any, Any]],
    task_handle: TaskHandle,
    exception_handler: ExceptionHandler | None = None,
    *,
    task_status: TaskStatus[Any] = TASK_STATUS_IGNORED,
) -> None:
    __tracebackhide__ = True  # trick supported by certain debugger frameworks

    # Check if the function has a parameter named "task_status"
    has_task_status = any(
        param.name == "task_status" and param.kind != Parameter.POSITIONAL_ONLY
        for param in signature(func).parameters.values()
    )
    logger.debug("Background task (%s) starting", task_handle.name)

    try:
        with task_handle._cancel_scope:
            async with Context():
                if has_task_status:
                    await func(task_status=task_status)
                else:
                    task_status.started()
                    await func()
    except BaseException as exc:
        if isinstance(exc, Exception):
            logger.exception("Background task (%s) crashed", task_handle.name)
            if exception_handler is not None and exception_handler(exc):
                return

        raise
    else:
        logger.debug("Background task (%s) finished successfully", task_handle.name)
    finally:
        task_handle._finished_event.set()


@dataclass
class TaskFactory:
    exception_handler: ExceptionHandler | None = None
    _finished_event: Event = field(init=False, default_factory=Event)
    _task_group: TaskGroup = field(init=False)
    _tasks: set[TaskHandle] = field(init=False, default_factory=set)

    def all_task_handles(self) -> set[TaskHandle]:
        """
        Return task handles for all the tasks currently running on the factory's task
        group.

        """
        return self._tasks.copy()

    async def start_task(
        self,
        func: Callable[..., Coroutine[Any, Any, T_Retval]],
        name: str | None = None,
    ) -> TaskHandle:
        """
        Start a background task in the factory's task group.

        The task runs in its own context, inherited from the root context.
        If the task raises an exception (inherited from :exc:`Exception`), it is logged
        with a descriptive message containing the task's name.

        To pass arguments to the target callable, pass them via lambda (e.g.
        ``lambda: yourfunc(arg1, arg2, kw=val)``)

        If ``func`` takes an argument named ``task_status``, then this method will only
        return when the function has called ``task_status.started()``. See
        :meth:`anyio.abc.TaskGroup.start` for details. The value passed to
        ``task_status.started()`` will be available as the ``start_value`` property on
        the :class:`TaskHandle`.

        :param func: the coroutine function to run
        :param name: descriptive name for the task
        :return: a task handle that can be used to await on the result or cancel the
            task

        """
        task_handle = TaskHandle(name=name or callable_name(func))
        self._tasks.add(task_handle)
        task_handle.start_value = await self._task_group.start(
            self._run_background_task,
            func,
            task_handle,
            self.exception_handler,
            name=task_handle.name,
        )
        return task_handle

    def start_task_soon(
        self,
        func: Callable[..., Coroutine[Any, Any, T_Retval]],
        name: str | None = None,
    ) -> TaskHandle:
        """
        Start a background task in the factory's task group.

        This is similar to :meth:`start_task`, but works from synchronous callbacks and
        doesn't support :class:`~anyio.abc.TaskStatus`.

        :param func: the coroutine function to run
        :param name: descriptive name for the task
        :return: a task handle that can be used to await on the result or cancel the
            task

        """
        task_handle = TaskHandle(name=name or callable_name(func))
        self._tasks.add(task_handle)
        self._task_group.start_soon(
            self._run_background_task,
            func,
            task_handle,
            self.exception_handler,
            name=task_handle.name,
        )
        return task_handle

    async def _run_background_task(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        task_handle: TaskHandle,
        exception_handler: ExceptionHandler | None = None,
        *,
        task_status: TaskStatus[Any] = TASK_STATUS_IGNORED,
    ) -> None:
        __tracebackhide__ = True  # trick supported by certain debugger frameworks
        try:
            await run_background_task(
                func, task_handle, exception_handler, task_status=task_status
            )
        finally:
            self._tasks.remove(task_handle)

    async def _run(self, *, task_status: TaskStatus[None]) -> None:
        async with create_task_group() as self._task_group:
            task_status.started()
            await self._finished_event.wait()
