from __future__ import annotations

import gc
import platform
import re
import signal
import sys
import textwrap
from collections.abc import Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from functools import partial
from logging import INFO, Logger, basicConfig, getLogger
from logging.config import dictConfig
from traceback import StackSummary
from types import FrameType
from typing import Any, cast
from warnings import warn

import anyio
from anyio import (
    CancelScope,
    Event,
    get_cancelled_exc_class,
    get_current_task,
    get_running_tasks,
    sleep,
    to_thread,
)
from anyio.abc import TaskStatus

from ._component import CLIApplicationComponent, Component, component_types
from ._concurrent import start_service_task
from ._context import Context
from ._utils import qualified_name

component_task_re = re.compile(r"^Starting (\S+) \((.+)\)$")


async def handle_signals(
    startup_scope: CancelScope, event: Event, *, task_status: TaskStatus[None]
) -> None:
    logger = getLogger(__name__)
    with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
        task_status.started()
        async for signum in signals:
            signal_name = signal.strsignal(signum) or ""
            logger.info(
                "Received signal (%s) – terminating application",
                signal_name.split(":", 1)[0],  # macOS has ": <signum>" after the name
            )
            startup_scope.cancel()
            event.set()


def get_coro_stack_summary(coro: Any) -> StackSummary:
    frames: list[FrameType] = []
    while isinstance(coro, Coroutine):
        while coro.__class__.__name__ == "async_generator_asend":
            # Hack to get past asend() objects
            coro = gc.get_referents(coro)[0].ag_await

        if frame := getattr(coro, "cr_frame", None):
            frames.append(frame)

        coro = getattr(coro, "cr_await", None)

    frame_tuples = [(f, f.f_lineno) for f in frames]
    return StackSummary.extract(frame_tuples)


async def startup_watcher(
    startup_cancel_scope: CancelScope,
    root_component: Component,
    start_timeout: float,
    logger: Logger,
    *,
    task_status: TaskStatus[CancelScope],
) -> None:
    current_task = get_current_task()
    parent_task = next(
        task_info
        for task_info in get_running_tasks()
        if task_info.id == current_task.parent_id
    )

    with CancelScope() as cancel_scope:
        task_status.started(cancel_scope)
        await sleep(start_timeout)

    if cancel_scope.cancel_called:
        return

    @dataclass
    class ComponentStatus:
        name: str
        alias: str | None
        parent_task_id: int | None
        traceback: list[str] = field(init=False, default_factory=list)
        children: list[ComponentStatus] = field(init=False, default_factory=list)

    component_statuses: dict[int, ComponentStatus] = {}
    for task in get_running_tasks():
        if task.id == parent_task.id:
            status = ComponentStatus(qualified_name(root_component), None, None)
        elif task.name and (match := component_task_re.match(task.name)):
            name: str
            alias: str
            name, alias = match.groups()
            status = ComponentStatus(name, alias, task.parent_id)
        else:
            continue

        status.traceback = get_coro_stack_summary(task.coro).format()
        component_statuses[task.id] = status

    root_status: ComponentStatus
    for task_id, component_status in component_statuses.items():
        if component_status.parent_task_id is None:
            root_status = component_status
        elif parent_status := component_statuses.get(component_status.parent_task_id):
            parent_status.children.append(component_status)
            if parent_status.alias:
                component_status.alias = (
                    f"{parent_status.alias}.{component_status.alias}"
                )

    def format_status(status_: ComponentStatus, level: int) -> str:
        title = f"{status_.alias or 'root'} ({status_.name})"
        if status_.children:
            children_output = ""
            for i, child in enumerate(status_.children):
                prefix = "| " if i < (len(status_.children) - 1) else "  "
                children_output += "+-" + textwrap.indent(
                    format_status(child, level + 1),
                    prefix,
                    lambda line: line[0] in " +|",
                )

            output = title + "\n" + children_output
        else:
            formatted_traceback = "".join(status_.traceback)
            if level == 0:
                formatted_traceback = textwrap.indent(formatted_traceback, "| ")

            output = title + "\n" + formatted_traceback

        return output

    logger.error(
        "Timeout waiting for the root component to start\n"
        "Components still waiting to finish startup:\n%s",
        textwrap.indent(format_status(root_status, 0).rstrip(), "  "),
    )
    startup_cancel_scope.cancel()


async def _run_application_async(
    component: Component,
    logger: Logger,
    max_threads: int | None,
    start_timeout: float | None,
) -> int:
    # Apply the maximum worker thread limit
    if max_threads is not None:
        to_thread.current_default_thread_limiter().total_tokens = max_threads

    logger.info("Starting application")
    try:
        async with AsyncExitStack() as exit_stack:
            event = Event()

            await exit_stack.enter_async_context(Context())
            with CancelScope() as startup_scope:
                if platform.system() != "Windows":
                    await start_service_task(
                        partial(handle_signals, startup_scope, event),
                        "Asphalt signal handler",
                    )

                try:
                    if start_timeout is not None:
                        startup_watcher_scope = await start_service_task(
                            lambda task_status: startup_watcher(
                                startup_scope,
                                component,
                                start_timeout,
                                logger,
                                task_status=task_status,
                            ),
                            "Asphalt startup watcher task",
                        )

                    await component.start()
                except get_cancelled_exc_class():
                    return 1
                except BaseException:
                    logger.exception("Error during application startup")
                    return 1

            startup_watcher_scope.cancel()
            logger.info("Application started")

            if isinstance(component, CLIApplicationComponent):
                exit_code = await component.run()
                if isinstance(exit_code, int):
                    if 0 <= exit_code <= 127:
                        return exit_code
                    else:
                        warn(f"exit code out of range: {exit_code}")
                        return 1
                elif exit_code is not None:
                    warn(
                        f"run() must return an integer or None, not "
                        f"{qualified_name(exit_code.__class__)}"
                    )
                    return 1
            else:
                await event.wait()

        return 0
    finally:
        logger.info("Application stopped")


def run_application(
    component: Component | dict[str, Any],
    *,
    backend: str = "asyncio",
    backend_options: dict[str, Any] | None = None,
    max_threads: int | None = None,
    logging: dict[str, Any] | int | None = INFO,
    start_timeout: int | float | None = 10,
) -> None:
    """
    Configure logging and start the given component.

    Assuming the root component was started successfully, the event loop will continue
    running until the process is terminated.

    Initializes the logging system first based on the value of ``logging``:
      * If the value is a dictionary, it is passed to :func:`logging.config.dictConfig`
        as argument.
      * If the value is an integer, it is passed to :func:`logging.basicConfig` as the
        logging level.
      * If the value is ``None``, logging setup is skipped entirely.

    By default, the logging system is initialized using :func:`~logging.basicConfig`
    using the ``INFO`` logging level.

    The default executor in the event loop is replaced with a new
    :class:`~concurrent.futures.ThreadPoolExecutor` where the maximum number of threads
    is set to the value of ``max_threads`` or, if omitted, the default value of
    :class:`~concurrent.futures.ThreadPoolExecutor`.

    :param component: the root component (either a component instance or a configuration
        dictionary where the special ``type`` key is a component class
    :param max_threads: the maximum number of worker threads in the default thread pool
        executor (the default value depends on the event loop implementation)
    :param logging: a logging configuration dictionary, :ref:`logging level
        <python:levels>` or`None``
    :param start_timeout: seconds to wait for the root component (and its subcomponents)
        to start up before giving up (``None`` = wait forever)

    """
    # Configure the logging system
    if isinstance(logging, dict):
        dictConfig(logging)
    elif isinstance(logging, int):
        basicConfig(level=logging)

    # Inform the user whether -O or PYTHONOPTIMIZE was set when Python was launched
    logger = getLogger(__name__)
    logger.info("Running in %s mode", "development" if __debug__ else "production")

    # Instantiate the root component if a dict was given
    if isinstance(component, dict):
        component = cast(Component, component_types.create_object(**component))

    if exit_code := anyio.run(
        _run_application_async,
        component,
        logger,
        max_threads,
        start_timeout,
        backend=backend,
        backend_options=backend_options,
    ):
        sys.exit(exit_code)
