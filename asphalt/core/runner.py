from concurrent.futures import ThreadPoolExecutor
from typing import Union, Dict, Any
from logging.config import dictConfig
from logging import basicConfig, getLogger, INFO
import asyncio
import os

from .concurrency import set_event_loop
from .component import Component
from .context import Context


def run_application(component: Component, *, max_threads: int=None,
                    logging: Union[Dict[str, Any], int, None]=INFO):
    """
    Starts the given top level component in the default asyncio event
    loop and keeps the event loop running until the process is
    terminated.

    Initializes the logging system first based on the value of
    ``logging``:
      * If the value is a dictionary, it is passed to
        :func:`logging.config.dictConfig` as argument.
      * If the value is an integer, it is passed to
        :func:`logging.basicConfig` as the logging  level.
      * If the value is ``None``, logging setup is skipped entirely.

    By default, the logging system is initialized using
    :func:`~logging.basicConfig` using the INFO logging level.

    The default executor in the event loop is replaced with a new
    :class:`~concurrent.futures.ThreadPoolExecutor` where the maximum
    number of threads is set to the value of ``max_threads`` or, if
    omitted, the return value of :func:`os.cpu_count()`.

    :param component: the top level component
    :param max_threads: the maximum number of threads in the thread
        pool
    :param logging: a logging configuration dictionary,
        :ref:`logging level <python:levels>` or ``None``

    """
    # Configure the logging system
    if isinstance(logging, dict):
        dictConfig(logging)
    elif isinstance(logging, int):
        basicConfig(level=logging)

    # Assign a new default executor with the given max worker thread
    # limit
    max_threads = max_threads if max_threads is not None else os.cpu_count()
    event_loop = asyncio.get_event_loop()
    event_loop.set_default_executor(ThreadPoolExecutor(max_threads))
    set_event_loop(event_loop)

    logger = getLogger(__name__)
    logger.info('Starting application')
    with Context() as context:
        try:
            # retval should be an awaitable or None
            retval = component.start(context)
            if retval is not None:
                event_loop.run_until_complete(retval)
        except BaseException:
            logger.exception('Error during application startup')
            raise

        # Finally, run the event loop until the process is terminated
        # or Ctrl+C is pressed
        logger.info('Application started')
        try:
            event_loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            pass

    logger.info('Application stopped')
    event_loop.close()
