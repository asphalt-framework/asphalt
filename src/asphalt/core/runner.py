from __future__ import annotations

__all__ = ("run_application",)

import asyncio
import signal
import sys
from asyncio.events import AbstractEventLoop
from concurrent.futures import ThreadPoolExecutor
from logging import INFO, Logger, basicConfig, getLogger, shutdown
from logging.config import dictConfig
from typing import Any, Dict, Optional, Union, cast

from asphalt.core.component import Component, component_types
from asphalt.core.context import Context, _current_context
from asphalt.core.utils import PluginContainer, qualified_name

policies = PluginContainer("asphalt.core.event_loop_policies")


def sigterm_handler(logger: Logger, event_loop: AbstractEventLoop) -> None:
    if event_loop.is_running():
        logger.info("Received SIGTERM")
        event_loop.stop()


def run_application(
    component: Union[Component, Dict[str, Any]],
    *,
    event_loop_policy: str | None = None,
    max_threads: int | None = None,
    logging: Union[Dict[str, Any], int, None] = INFO,
    start_timeout: Union[int, float, None] = 10,
) -> None:
    """
    Configure logging and start the given root component in the default asyncio event loop.

    Assuming the root component was started successfully, the event loop will continue running
    until the process is terminated.

    Initializes the logging system first based on the value of ``logging``:
      * If the value is a dictionary, it is passed to :func:`logging.config.dictConfig` as
        argument.
      * If the value is an integer, it is passed to :func:`logging.basicConfig` as the logging
        level.
      * If the value is ``None``, logging setup is skipped entirely.

    By default, the logging system is initialized using :func:`~logging.basicConfig` using the
    ``INFO`` logging level.

    The default executor in the event loop is replaced with a new
    :class:`~concurrent.futures.ThreadPoolExecutor` where the maximum number of threads is set to
    the value of ``max_threads`` or, if omitted, the default value of
    :class:`~concurrent.futures.ThreadPoolExecutor`.

    :param component: the root component (either a component instance or a configuration dictionary
        where the special ``type`` key is either a component class or a ``module:varname``
        reference to one)
    :param event_loop_policy: entry point name (from the ``asphalt.core.event_loop_policies``
        namespace) of an alternate event loop policy (or a module:varname reference to one)
    :param max_threads: the maximum number of worker threads in the default thread pool executor
        (the default value depends on the event loop implementation)
    :param logging: a logging configuration dictionary, :ref:`logging level <python:levels>` or
        ``None``
    :param start_timeout: seconds to wait for the root component (and its subcomponents) to start
        up before giving up (``None`` = wait forever)

    """
    # Configure the logging system
    if isinstance(logging, dict):
        dictConfig(logging)
    elif isinstance(logging, int):
        basicConfig(level=logging)

    # Inform the user whether -O or PYTHONOPTIMIZE was set when Python was launched
    logger = getLogger(__name__)
    logger.info("Running in %s mode", "development" if __debug__ else "production")

    # Switch to an alternate event loop policy if one was provided
    if event_loop_policy:
        create_policy = policies.resolve(event_loop_policy)
        policy = create_policy()
        asyncio.set_event_loop_policy(policy)
        logger.info("Switched event loop policy to %s", qualified_name(policy))

    # Assign a new default executor with the given max worker thread limit if one was provided
    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)
    try:
        if max_threads is not None:
            event_loop.set_default_executor(ThreadPoolExecutor(max_threads))
            logger.info(
                "Installed a new thread pool executor with max_workers=%d", max_threads
            )

        # Instantiate the root component if a dict was given
        if isinstance(component, dict):
            component = cast(Component, component_types.create_object(**component))

        logger.info("Starting application")
        context = Context()
        exception: Optional[BaseException] = None
        exit_code = 0

        # Start the root component
        token = _current_context.set(context)
        try:
            try:
                coro = asyncio.wait_for(component.start(context), start_timeout)
                event_loop.run_until_complete(coro)
            except asyncio.TimeoutError as e:
                exception = e
                logger.error("Timeout waiting for the root component to start")
                exit_code = 1
            except Exception as e:
                exception = e
                logger.exception("Error during application startup")
                exit_code = 1
            else:
                logger.info("Application started")

                # Add a signal handler to gracefully deal with SIGTERM
                try:
                    event_loop.add_signal_handler(
                        signal.SIGTERM, sigterm_handler, logger, event_loop
                    )
                except NotImplementedError:
                    pass  # Windows does not support signals very well

                # Finally, run the event loop until the process is terminated or Ctrl+C
                # is pressed
                try:
                    event_loop.run_forever()
                except KeyboardInterrupt:
                    pass
                except SystemExit as e:
                    exit_code = e.code

            # Close the root context
            logger.info("Stopping application")
            event_loop.run_until_complete(context.close(exception))
        finally:
            _current_context.reset(token)

        # Shut down leftover async generators
        event_loop.run_until_complete(event_loop.shutdown_asyncgens())
    finally:
        # Finally, close the event loop itself
        event_loop.close()
        asyncio.set_event_loop(None)
        logger.info("Application stopped")

    # Shut down the logging system
    shutdown()

    if exit_code:
        sys.exit(exit_code)
