from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Union
from logging import getLogger
import logging.config
import threading
import asyncio

from pkg_resources import iter_entry_points, EntryPoint

from .component import Component
from .context import Context, ApplicationContext
from . import util

__all__ = 'Application',


class Application(Component):
    """
    This class orchestrates the configuration and setup of the chosen Asphalt components.

    :param components: a dictionary of component identifier -> constructor arguments
    :param max_threads: maximum number of worker threads
    :param logging: logging configuration dictionary for :func:`~logging.config.dictConfig`
                    or a boolean (``True`` = call :func:`~logging.basicConfig`,
                    ``False`` = skip logging configuration)
    :param settings: application specific configuration options (available as ctx.settings)
    """

    __slots__ = ('components', 'settings', 'component_types', 'component_config', 'max_threads',
                 'logging_config', 'logger')

    def __init__(self, components: Dict[str, Dict[str, Any]]=None, *, max_threads: int=8,
                 logging: Union[Dict[str, Any], bool]=True, settings: dict=None):
        assert max_threads > 0, 'max_threads must be a positive integer'
        self.components = []
        self.settings = settings
        self.component_types = {ep.name: ep for ep in iter_entry_points('asphalt.components')}
        self.component_config = components or {}
        self.max_threads = max_threads
        self.logging_config = logging
        self.logger = getLogger('asphalt.core')

    def create_context(self) -> ApplicationContext:
        return ApplicationContext(self.settings)

    def add_component(self, component_alias: str, component_class: type=None, **component_kwargs):
        """
        Instantiates a component and adds it to the component list used by this application.

        The first argument can either be a :class:`~asphalt.core.component.Component` subclass or a
        component type name, declared as an ``asphalt.components`` entry point, in which case the
        component class is retrieved by loading the entry point.

        The locally given configuration can be overridden by component configuration parameters
        supplied to the Application constructor (the ``components`` argument).
        """

        if not isinstance(component_alias, str) or not component_alias:
            raise TypeError('component_alias must be a nonempty string')

        if component_class is None:
            if component_alias not in self.component_types:
                raise LookupError('no such component type: ' + component_alias) from None

            component_class = self.component_types[component_alias]
            if isinstance(component_class, EntryPoint):
                component_class = self.component_types[component_alias] = component_class.load()

        if not issubclass(component_class, Component):
            raise TypeError('the component class must be a subclass of asphalt.core.Component')

        # Apply the modifications to the hard coded configuration from the external config
        component_kwargs.update(self.component_config.get(component_alias, {}))

        component = component_class(**component_kwargs)
        self.components.append(component)

    def start(self, ctx: Context):
        # Start all the components and return a Future which completes when all the components
        # have started
        tasks = [retval for retval in (component.start(ctx) for component in self.components)
                 if retval is not None]
        return asyncio.gather(*tasks)

    def run(self):
        # Configure the logging system
        if isinstance(self.logging_config, dict):
            logging.config.dictConfig(self.logging_config)
        elif self.logging_config:
            logging.basicConfig(level=logging.INFO)

        # Assign a new default executor with the given max worker thread limit
        event_loop = asyncio.get_event_loop()
        event_loop.set_default_executor(ThreadPoolExecutor(self.max_threads))

        # This is necessary to make @asynchronous work
        util.event_loop = event_loop
        util.event_loop_thread_id = threading.get_ident()

        # Create the application context
        context = self.create_context()

        try:
            # Call the application's start() method.
            # If it returns an awaitable, run the event loop until it's done.
            self.logger.info('Starting application')
            retval = self.start(context)
            if retval is not None:
                event_loop.run_until_complete(retval)

            # Run all the application context's start callbacks
            event_loop.run_until_complete(context.dispatch('started'))
            self.logger.info('Application started')
        except Exception as exc:
            self.logger.exception('Error during application startup')
            context.exception = exc
        else:
            # Finally, run the event loop until the process is terminated or Ctrl+C is pressed
            try:
                event_loop.run_forever()
            except (KeyboardInterrupt, SystemExit):
                pass

        event_loop.run_until_complete(context.dispatch('finished'))
        event_loop.close()
        self.logger.info('Application stopped')
