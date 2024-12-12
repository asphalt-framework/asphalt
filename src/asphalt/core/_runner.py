from __future__ import annotations

import platform
import signal
import sys
from functools import partial
from logging import INFO, basicConfig, getLogger
from logging.config import dictConfig
from typing import Any
from warnings import warn

import anyio
from anyio import (
    CancelScope,
    Event,
    get_cancelled_exc_class,
    to_thread,
)
from anyio.abc import TaskStatus

from . import start_service_task
from ._component import (
    CLIApplicationComponent,
    Component,
    start_component,
)
from ._context import Context
from ._utils import qualified_name

logger = getLogger("asphalt.core")


async def handle_signals(
    startup_scope: CancelScope, event: Event, *, task_status: TaskStatus[None]
) -> None:
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
            break


async def _run_application_async(
    component_class: type[Component] | str,
    config: dict[str, Any] | None,
    max_threads: int | None,
    start_timeout: float | None,
) -> int:
    # Apply the maximum worker thread limit
    if max_threads is not None:
        to_thread.current_default_thread_limiter().total_tokens = max_threads

    logger.info("Starting application")
    try:
        event = Event()
        async with Context():
            with CancelScope() as startup_scope:
                if platform.system() != "Windows":
                    await start_service_task(
                        partial(handle_signals, startup_scope, event),
                        "Asphalt signal handler",
                    )

                try:
                    component = await start_component(
                        component_class, config, timeout=start_timeout
                    )
                except (get_cancelled_exc_class(), TimeoutError):
                    # This happens when a signal handler cancels the startup or
                    # start_component() times out
                    return 1
                except BaseException:
                    logger.exception("Error during application startup")
                    return 1

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
    component_class: type[Component] | str,
    config: dict[str, Any] | None = None,
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

    :param component_class: the root component class, an entry point name in the
        ``asphalt.components`` namespace or a ``modulename:varname`` reference
    :param config: configuration options for the root component
    :param backend: name of the AnyIO backend (e.g. ``asyncio`` or ``trio``)
    :param backend_options: options to pass to the AnyIO backend (see the
        `AnyIO documentation`_ for reference)
    :param max_threads: the maximum number of worker threads in the default thread pool
        executor (the default value depends on the event loop implementation)
    :param logging: a logging configuration dictionary, :ref:`logging level
        <python:levels>` or`None``
    :param start_timeout: seconds to wait for the root component (and its subcomponents)
        to start up before giving up (``None`` = wait forever)
    :raises SystemExit: if the root component fails to start, or an exception is raised
        when the application is running

    .. _AnyIO documentation: https://anyio.readthedocs.io/en/stable/basics.html\
    #backend-specific-options

    """
    # Configure the logging system
    if isinstance(logging, dict):
        dictConfig(logging)
    elif isinstance(logging, int):
        basicConfig(level=logging)

    # Inform the user whether -O or PYTHONOPTIMIZE was set when Python was launched
    logger.info("Running in %s mode", "development" if __debug__ else "production")

    if exit_code := anyio.run(
        _run_application_async,
        component_class,
        config,
        max_threads,
        start_timeout,
        backend=backend,
        backend_options=backend_options,
    ):
        sys.exit(exit_code)
