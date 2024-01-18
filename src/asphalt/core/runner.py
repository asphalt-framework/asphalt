from __future__ import annotations

__all__ = ("run_application",)

import asyncio
import sys
from asyncio.events import AbstractEventLoop
from logging import INFO, Logger, basicConfig, getLogger, shutdown
from logging.config import dictConfig
from traceback import print_exception
from typing import Any, cast

from anyio import create_task_group, fail_after

from .component import CLIApplicationComponent, Component, component_types
from .context import Context, _current_context
from .utils import PluginContainer, qualified_name

policies = PluginContainer("asphalt.core.event_loop_policies")


def sigterm_handler(logger: Logger, event_loop: AbstractEventLoop) -> None:
    if event_loop.is_running():
        logger.info("Received SIGTERM")
        event_loop.stop()


async def run_application(
    component: Component | dict[str, Any],
    *,
    event_loop_policy: str | None = None,
    logging: dict[str, Any] | int | None = INFO,
    start_timeout: int | float | None = 10,
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

    :param component: the root component (either a component instance or a configuration dictionary
        where the special ``type`` key is either a component class or a ``module:varname``
        reference to one)
    :param event_loop_policy: entry point name (from the ``asphalt.core.event_loop_policies``
        namespace) of an alternate event loop policy (or a module:varname reference to one)
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

    # Instantiate the root component if a dict was given
    if isinstance(component, dict):
        component = cast(Component, component_types.create_object(**component))

    logger.info("Starting application")
    context = Context()
    exception: BaseException | None = None
    exit_code = 0

    # Start the root component
    token = _current_context.set(context)
    try:
        async with create_task_group() as tg:
            component._task_group = tg
            try:
                with fail_after(start_timeout):
                    await component.start(context)
            except TimeoutError as e:
                exception = e
                logger.error("Timeout waiting for the root component to start")
                exit_code = 1
            except Exception as e:
                exception = e
                logger.exception("Error during application startup")
                exit_code = 1
            else:
                logger.info("Application started")
                if isinstance(component, CLIApplicationComponent):
                    exit_code = await component._exit_code.receive()
    except Exception as e:
        exception = e
        exit_code = 1
    finally:
        # Close the root context
        logger.info("Stopping application")
        await context.close(exception)
        _current_context.reset(token)
        logger.info("Application stopped")

    # Shut down the logging system
    shutdown()

    if exception is not None:
        print_exception(type(exception), exception, exception.__traceback__)

    if exit_code:
        sys.exit(exit_code)
