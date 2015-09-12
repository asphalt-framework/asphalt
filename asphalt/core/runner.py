from concurrent.futures import ThreadPoolExecutor
from typing import Union, Dict, Any
from logging.config import dictConfig
from logging import basicConfig, getLogger, INFO
import threading
import asyncio
import os

from .component import Component
from .context import Context
from . import util


def run_application(component: Component, *, max_threads: int=None,
                    logging: Union[Dict[str, Any], bool]=True):
    """
    Starts the given top level component in the default asyncio event loop and keeps the event loop
    running until the process is terminated.

    Initializes the logging system first unless ``logging`` is ``False``.
    If the value is a dictionary, it is passed to :func:`logging.config.dictConfig`.

    The default executor in the event loop is replaced with a new :class:`ThreadPoolExecutor` where
    the maximum number of threads is set to the value of ``max_threads`` or, if omitted, the return
    value of :func:`os.cpu_count()`.

    :param component: the top level component
    :param max_threads: the maximum number of threads in the thread pool
    :param logging: a logging configuration dictionary or ``True`` or ``False``
    """

    # Configure the logging system
    if isinstance(logging, dict):
        dictConfig(logging)
    elif logging:
        basicConfig(level=INFO)

    # Assign a new default executor with the given max worker thread limit
    max_threads = max_threads if max_threads is not None else os.cpu_count()
    event_loop = asyncio.get_event_loop()
    event_loop.set_default_executor(ThreadPoolExecutor(max_threads))

    # This is a necessary evil to make @asynchronous work
    util.event_loop = event_loop
    util.event_loop_thread_id = threading.get_ident()

    # Create the top level context
    context = Context()

    logger = getLogger(__name__)
    logger.info('Starting application')
    try:
        # Call the component's start() method.
        # If it returns a value, assume it is an awaitable and run the event loop until it's done.
        retval = component.start(context)
        if retval is not None:
            event_loop.run_until_complete(retval)

        # Run all the top level context's start callbacks
        event_loop.run_until_complete(context.dispatch('started'))
    except Exception as exc:
        logger.exception('Error during application startup')
        context.exception = exc
    else:
        # Finally, run the event loop until the process is terminated or Ctrl+C is pressed
        logger.info('Application started')
        try:
            event_loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            pass

    event_loop.run_until_complete(context.dispatch('finished'))
    event_loop.close()
    logger.info('Application stopped')
