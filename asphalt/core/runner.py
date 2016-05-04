import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from logging import basicConfig, getLogger, INFO
from logging.config import dictConfig
from typing import Union, Dict, Any

from typeguard import check_argument_types

from asphalt.core.component import Component
from asphalt.core.context import Context
from asphalt.core.util import PluginContainer

__all__ = ('run_application',)

policies = PluginContainer('asphalt.core.event_loop_policies')


def uvloop_policy():
    import uvloop
    return uvloop.EventLoopPolicy()


def run_application(component: Component, *, event_loop_policy: str = None,
                    max_threads: int = None, logging: Union[Dict[str, Any], int, None] = INFO):
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
    the value of ``max_threads`` or, if omitted, the return value of :func:`os.cpu_count()`.

    :param component: the root component
    :param event_loop_policy: entry point name (from the ``asphalt.core.event_loop_policies``
        namespace) of an alternate event loop policy (or a module:varname reference to one)
    :param max_threads: the maximum number of worker threads in the default thread pool executor
    :param logging: a logging configuration dictionary, :ref:`logging level <python:levels>` or
        ``None``

    """
    assert check_argument_types()

    # Configure the logging system
    if isinstance(logging, dict):
        dictConfig(logging)
    elif isinstance(logging, int):
        basicConfig(level=logging)

    # Switch to an alternate event loop policy if one is provided
    if event_loop_policy:
        create_policy = policies.resolve(event_loop_policy)
        asyncio.set_event_loop_policy(create_policy())

    # Assign a new default executor with the given max worker thread limit
    max_threads = max_threads if max_threads is not None else os.cpu_count()
    event_loop = asyncio.get_event_loop()
    event_loop.set_default_executor(ThreadPoolExecutor(max_threads))

    logger = getLogger(__name__)
    logger.info('Starting application')
    context = Context()
    exception = None
    try:
        try:
            event_loop.run_until_complete(component.start(context))
        except Exception as e:
            exception = e
            logger.exception('Error during application startup')
        else:
            # Enable garbage collection of the component tree
            del component

            # Finally, run the event loop until the process is terminated or Ctrl+C is pressed
            event_loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        event_loop.run_until_complete(context.dispatch_event('finished', exception))

    event_loop.close()
    logger.info('Application stopped')

    if exception is not None:
        sys.exit(1)
