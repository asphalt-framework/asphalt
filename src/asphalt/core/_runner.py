from __future__ import annotations

import signal
import sys
from logging import INFO, basicConfig, getLogger
from logging.config import dictConfig
from traceback import format_stack
from typing import Any, cast

import anyio
from anyio import (
    BrokenResourceError,
    WouldBlock,
    create_memory_object_stream,
    create_task_group,
    get_running_tasks,
    to_thread,
)
from anyio.abc import TaskGroup, TaskStatus
from anyio.streams.memory import MemoryObjectSendStream

from ._component import Component, component_types
from ._context import Context, inject, resource


async def handle_signals(*, task_status: TaskStatus) -> None:
    logger = getLogger(__name__)
    with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
        task_status.started()
        async for signum in signals:
            logger.info(
                "Received signal (%s) – terminating application",
                signal.strsignal(signum),
            )
            stop_application()
            return


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
            send, receive = create_memory_object_stream(1, int)
            await context.add_resource(send, "exit_code_stream")
            await context.add_resource(root_tg, "root_taskgroup", [TaskGroup])
            await root_tg.start(handle_signals, name="Asphalt signal handler")
            existing_task_ids = {task.id for task in get_running_tasks()}
            try:
                with anyio.fail_after(start_timeout):
                    await component.start(context)
            except TimeoutError:
                tracebacks: list[tuple[str, str]] = []
                for task in get_running_tasks():
                    if task.id not in existing_task_ids:
                        frame = getattr(task.coro, "cr_frame", None)
                        task_tb = "".join(format_stack(frame)) if frame else "(unknown)"
                        tracebacks.append((task.name, task_tb))

                joined_tracebacks = "\n".join(
                    f"Task {task_name!r}:\n{task_tb}"
                    for task_name, task_tb in tracebacks
                )
                logger.error(
                    "Timeout waiting for the root component to start – exiting.\n"
                    "Stack traces of %d tasks still running:\n%s",
                    len(tracebacks),
                    joined_tracebacks,
                )
                raise
            except Exception:
                logger.exception("Error during application startup")
                raise

            logger.info("Application started")
            exit_code = await receive.receive()
            if exit_code:
                sys.exit(exit_code)
            else:
                root_tg.cancel_scope.cancel()
    finally:
        logger.info("Application stopped")


@inject
def stop_application(
    exit_code: int = 0,
    *,
    exit_code_stream: MemoryObjectSendStream = resource("exit_code_stream"),
) -> None:
    """Trigger an orderly shutdown of the application."""
    try:
        exit_code_stream.send_nowait(exit_code)
    except (WouldBlock, BrokenResourceError):
        pass  # Something else already called this
