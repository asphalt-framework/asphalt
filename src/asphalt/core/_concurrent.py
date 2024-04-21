from __future__ import annotations

import logging
import sys
from collections.abc import Coroutine
from dataclasses import dataclass, field
from inspect import Parameter, isawaitable, signature
from typing import Any, Callable, Literal, TypeVar, Union

from anyio import (
    TASK_STATUS_IGNORED,
    CancelScope,
    Event,
    create_task_group,
)
from anyio.abc import TaskGroup, TaskStatus

from ._context import Context, ContextState, current_context
from ._utils import callable_name

if sys.version_info >= (3, 10):
    from typing import TypeAlias
else:
    from typing_extensions import TypeAlias

T_Retval = TypeVar("T_Retval")
TeardownAction: TypeAlias = Union[Callable[[], Any], Literal["cancel"], None]
ExceptionHandler: TypeAlias = Callable[[Exception], bool]

logger = logging.getLogger(__name__)


@dataclass
class TaskHandle:
    """
    A representation of a task started from :class:`TaskFactory`.

    :ivar name: the name of the task
    :ivar start_value: the start value passed to ``task_status.started()`` if the target
        function supported that
    """

    name: str
    start_value: Any = field(init=False, repr=False)
    _cancel_scope: CancelScope = field(
        init=False, default_factory=CancelScope, repr=False
    )
    _finished_event: Event = field(init=False, default_factory=Event, repr=False)

    def cancel(self) -> None:
        """Schedule the task to be cancelled."""
        self._cancel_scope.cancel()

    async def wait_finished(self) -> None:
        """Wait until the task is finished."""
        await self._finished_event.wait()

    def __hash__(self) -> int:
        return hash(self._cancel_scope)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TaskHandle):
            return self._cancel_scope is other._cancel_scope

        return NotImplemented


async def _run_background_task(
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
            await _run_background_task(
                func, task_handle, exception_handler, task_status=task_status
            )
        finally:
            self._tasks.remove(task_handle)

    async def _run(self, *, task_status: TaskStatus[None]) -> None:
        async with create_task_group() as self._task_group:
            task_status.started()
            await self._finished_event.wait()


async def start_service_task(
    func: Callable[..., Coroutine[Any, Any, T_Retval]],
    name: str,
    *,
    teardown_action: TeardownAction = "cancel",
) -> Any:
    """
    Start a background task that gets shut down when the context shuts down.

    This method is meant to be used by components to run their tasks like network
    services that should be shut down with the application, because each call to this
    functions registers a context teardown callback that waits for the service task to
    finish before allowing the context teardown to continue..

    If you supply a teardown callback, and it raises an exception, then the task
    will be cancelled instead.

    :param func: the coroutine function to run
    :param name: descriptive name (e.g. "HTTP server") for the task, to which the
        prefix "Service task: " will be added when the task is actually created
        in the backing asynchronous event loop implementation (e.g. asyncio)
    :param teardown_action: the action to take when the context is being shut down:

        * ``'cancel'``: cancel the task
        * ``None``: no action (the task must finish by itself)
        * (function, or any callable, can be asynchronous): run this callable to signal
            the task to finish
    :return: any value passed to ``task_status.started()`` by the target callable if
        it supports that, otherwise ``None``
    """

    async def finalize_service_task() -> None:
        if teardown_action == "cancel":
            logger.debug("Cancelling service task %r", name)
            task_handle.cancel()
        elif teardown_action is not None:
            teardown_action_name = callable_name(teardown_action)
            logger.debug(
                "Calling teardown callback (%s) for service task %r",
                teardown_action_name,
                name,
            )
            try:
                retval = teardown_action()
                if isawaitable(retval):
                    await retval
            except BaseException as exc:
                task_handle.cancel()
                if isinstance(exc, Exception):
                    logger.exception(
                        "Error calling teardown callback (%s) for service task %r",
                        teardown_action_name,
                        name,
                    )

        logger.debug("Waiting for service task %r to finish", name)
        await task_handle.wait_finished()
        logger.debug("Service task %r finished", name)

    ctx = current_context()
    while ctx.parent is not None:
        ctx = ctx.parent

    ctx._ensure_state(ContextState.open)

    if (
        teardown_action != "cancel"
        and teardown_action is not None
        and not callable(teardown_action)
    ):
        raise ValueError(
            "teardown_action must be a callable, None, or the string 'cancel'"
        )

    task_handle = TaskHandle(f"Service task: {name}")
    task_handle.start_value = await ctx._task_group.start(
        _run_background_task, func, task_handle, name=task_handle.name
    )
    ctx.add_teardown_callback(finalize_service_task)
    return task_handle.start_value


async def start_background_task_factory(
    *, exception_handler: ExceptionHandler | None = None
) -> TaskFactory:
    """
    Start a service task that hosts ad-hoc background tasks.

    Each of the tasks started by this factory is run in its own, separate Asphalt
    context, inherited from this context.

    When the service task is torn down, it will wait for all the background tasks to
    finish before returning.

    It is imperative to ensure that the task factory is set up after any of the
    resources potentially needed by the ad-hoc tasks are set up first. Failing to do
    so risks those resources being removed from the context before all the tasks
    have finished.

    :param exception_handler: a callback called to handle an exception raised from the
        task. Takes the exception (:exc:`Exception`) as the argument, and should return
        ``True`` if it successfully handled the exception.
    :return: the task factory

    .. seealso:: :func:`start_service_task`

    """
    factory = TaskFactory(exception_handler)
    await start_service_task(
        factory._run,
        f"Background task factory ({id(factory):x})",
        teardown_action=factory._finished_event.set,
    )
    return factory
