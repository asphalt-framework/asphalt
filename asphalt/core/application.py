from asyncio import AbstractEventLoop, get_event_loop
from concurrent.futures import ThreadPoolExecutor
from logging import getLogger
from typing import Dict, Any, Union, List
import logging.config
import asyncio

from pkg_resources import iter_entry_points, EntryPoint

from .component import Component
from .context import ApplicationContext, ContextEventType
from .util import resolve_reference

__all__ = 'Application',


class Application:
    """
    This class orchestrates the configuration and setup of the chosen Asphalt components.

    :param components: a dictionary of component identifier -> constructor arguments
    :param max_threads: maximum number of worker threads
    :param logging: logging configuration dictionary for :func:`~logging.config.dictConfig`
                    or a boolean (``True`` = call :func:`~logging.basicConfig`,
                    ``False`` = skip logging configuration)
    :param settings: application specific configuration options (available as ctx.settings)
    """

    __slots__ = ('settings', 'component_types', 'component_options', 'max_threads',
                 'logging_config', 'logger')

    def __init__(self, components: Dict[str, Dict[str, Any]]=None, *, max_threads: int=8,
                 logging: Union[Dict[str, Any], bool]=True, settings: dict=None):
        assert max_threads > 0, 'max_threads must be a positive integer'
        self.settings = settings
        self.component_types = {ep.name: ep for ep in iter_entry_points('asphalt.components')}
        self.component_options = components or {}
        self.max_threads = max_threads
        self.logging_config = logging
        self.logger = getLogger('asphalt.core')

    def create_context(self) -> ApplicationContext:
        return ApplicationContext(self.settings)

    def create_components(self) -> List[Component]:
        components = []
        for name, config in self.component_options.items():
            assert isinstance(config, dict)
            if 'class' in config:
                component_class = resolve_reference(config.pop('class'))
            elif name in self.component_types:
                component_class = self.component_types[name]
                if isinstance(component_class, EntryPoint):
                    component_class = component_class.load()
            else:
                raise LookupError('no such component: {}'.format(name))

            assert issubclass(component_class, Component)
            component = component_class(**config)
            components.append(component)

        return components

    def start(self, app_ctx: ApplicationContext):
        """
        This method should be overridden to implement custom application logic.
        It is called after all the components have initialized.
        It can be a coroutine.
        """

    def run(self, event_loop: AbstractEventLoop=None):
        # Configure the logging system
        if isinstance(self.logging_config, dict):
            logging.config.dictConfig(self.logging_config)
        elif self.logging_config:
            logging.basicConfig(level=logging.INFO)

        # Assign a new default executor with the given max worker thread limit
        event_loop = event_loop or get_event_loop()
        event_loop.set_default_executor(ThreadPoolExecutor(self.max_threads))

        # Create the application context
        context = self.create_context()

        try:
            # Start all the components and run the loop until they've finished
            self.logger.info('Starting components')
            components = self.create_components()
            coroutines = [coro for coro in (component.start(context) for component in components)
                          if coro is not None]
            event_loop.run_until_complete(asyncio.gather(*coroutines))
            self.logger.info('All components started')

            # Run the application's custom startup code
            coro = self.start(context)
            if coro is not None:
                event_loop.run_until_complete(coro)

            # Run all the application context's start callbacks
            event_loop.run_until_complete(context.run_callbacks(ContextEventType.started))
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

        event_loop.run_until_complete(context.run_callbacks(ContextEventType.finished))
        event_loop.close()
        self.logger.info('Application stopped')
