from __future__ import annotations

import inspect
import platform
import signal
from logging import INFO, basicConfig, getLogger
from logging.config import dictConfig
from typing import Any, cast

import anyio
from anyio import (
    Event,
    create_task_group,
    get_cancelled_exc_class,
    get_current_task,
    get_running_tasks,
    move_on_after,
    sleep,
    to_thread,
)
from anyio.abc import TaskGroup, TaskStatus

from ._component import Component, component_types
from ._context import Context
from ._exceptions import ApplicationExit
from ._utils import get_coro_frames


class ApplicationStartTimeoutError(Exception):
    def __init__(self, tracebacks: list[tuple[str, str]]):
        super().__init__(tracebacks)
        self.tracebacks = tracebacks


async def handle_signals(*, task_status: TaskStatus) -> None:
    logger = getLogger(__name__)
    with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
        task_status.started()
        async for signum in signals:
            signal_name = signal.strsignal(signum) or ""
            logger.info(
                "Received signal (%s) – terminating application",
                signal_name.split(":", 1)[0],  # macOS has ": <signum>" after the name
            )
            raise ApplicationExit


async def fail_on_timeout(
    start_timeout: float, started_event: Event, *, task_status: TaskStatus
) -> None:
    parent_task_id = get_current_task().parent_id
    existing_task_ids = {
        task.id for task in get_running_tasks() if task.id != parent_task_id
    }
    task_status.started()
    with move_on_after(start_timeout):
        await started_event.wait()
        return

    # Execution reaches here if the root component does not start on time
    tracebacks: list[tuple[str, str]] = []
    for task in get_running_tasks():
        if task.id not in existing_task_ids:
            formatted_frames: list[str] = []
            for i, frame in enumerate(get_coro_frames(task.coro)):
                module = inspect.getmodule(frame)
                sourcelines, start_line = inspect.getsourcelines(frame)
                sourceline = sourcelines[frame.f_lineno - start_line].strip()
                formatted_frames.append(
                    f"  {module.__name__}:{frame.f_lineno} -> {sourceline}"
                    if module
                    else "unknown"
                )

            task_tb = "\n".join(formatted_frames)
            tracebacks.append((task.name, task_tb))

    raise ApplicationStartTimeoutError(tracebacks)


async def run_application(
    component: Component | dict[str, Any],
    *,
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

    # Apply the maximum worker thread limit
    if max_threads is not None:
        to_thread.current_default_thread_limiter().total_tokens = max_threads

    # Instantiate the root component if a dict was given
    if isinstance(component, dict):
        component = cast(Component, component_types.create_object(**component))

    logger.info("Starting application")
    try:
        async with Context() as context, create_task_group() as root_tg:
            await context.add_resource(root_tg, "root_taskgroup", [TaskGroup])
            if platform.system() != "Windows":
                await root_tg.start(handle_signals, name="Asphalt signal handler")

            try:
                async with create_task_group() as startup_tg:
                    started_event = Event()
                    await startup_tg.start(
                        fail_on_timeout, start_timeout, started_event
                    )
                    await component.start(context)
                    started_event.set()
            except ApplicationStartTimeoutError as exc:
                joined_tracebacks = "\n".join(
                    f"Task {task_name!r}:\n{task_tb}"
                    for task_name, task_tb in exc.tracebacks
                )
                logger.error(
                    "Timeout waiting for the root component to start – exiting.\n"
                    "Stack traces of %d tasks still running:\n%s",
                    len(exc.tracebacks),
                    joined_tracebacks,
                )
                raise TimeoutError
            except (ApplicationExit, get_cancelled_exc_class()):
                logger.error("Application startup interrupted")
                return
            except BaseException:
                logger.exception("Error during application startup")
                raise

            logger.info("Application started")
            await sleep(float("inf"))
    except ApplicationExit as exc:
        if exc.code:
            raise SystemExit(exc.code).with_traceback(exc.__traceback__) from None
    finally:
        logger.info("Application stopped")
