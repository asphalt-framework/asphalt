from asyncio import coroutine, new_event_loop, set_event_loop, get_event_loop
from unittest.mock import Mock
import sys

from pkg_resources import EntryPoint
import pytest

from asphalt.core.application import Application
from asphalt.core.component import Component
from asphalt.core.context import ApplicationContext, ContextEventType
from asphalt.core.util import asynchronous


class ShutdownAPI:
    def __init__(self, app_ctx: ApplicationContext, method: str):
        self.app_ctx = app_ctx
        self.method = method

    @asynchronous
    def shutdown(self):
        def callback():
            if self.method == 'stop':
                get_event_loop().stop()
            elif self.method == 'exit':
                sys.exit()
            else:
                raise BaseException('this should crash the application')

        def schedule(ctx):
            event_loop.call_later(0.1, callback)

        event_loop = get_event_loop()
        self.app_ctx.add_callback(ContextEventType.started, schedule)


class ShutdownComponent(Component):
    def __init__(self, method: str):
        self.method = method

    def start(self, app_ctx: ApplicationContext):
        app_ctx.resources.add(ShutdownAPI(app_ctx, self.method), context_var='shutter')


class CustomApp(Application):
    def __init__(self, components, **kwargs):
        super().__init__(components, **kwargs)
        self.start_callback_called = False
        self.finish_callback_called = False

    @coroutine
    def start(self, app_ctx: ApplicationContext):
        def started_callback(ctx):
            self.start_callback_called = True

        def finished_callback(ctx):
            self.finish_callback_called = True

        app_ctx.add_callback(ContextEventType.started, started_callback)
        app_ctx.add_callback(ContextEventType.finished, finished_callback)
        app_ctx.shutter.shutdown()


class TestApplication:
    @pytest.fixture
    def event_loop(self):
        event_loop = new_event_loop()
        set_event_loop(event_loop)
        return event_loop

    @pytest.fixture
    def app(self):
        app = CustomApp({'shutdown': {'method': 'stop'}})
        app.component_types['shutdown'] = ShutdownComponent
        return app

    @pytest.mark.parametrize('add_method', ['specify_class', 'add_type', 'add_entrypoint'])
    def test_create_components(self, add_method):
        components_config = {
            'shutdown': {'method': 'stop'}
        }
        if add_method == 'specify_class':
            components_config['shutdown']['class'] = '{}:{}'.format(
                __spec__.name, ShutdownComponent.__name__)

        app = CustomApp(components_config)
        if add_method == 'add_type':
            app.component_types['shutdown'] = ShutdownComponent
        elif add_method == 'add_entrypoint':
            entrypoint = EntryPoint('shutdown', __spec__.name)
            entrypoint.load = Mock(return_value=ShutdownComponent)
            app.component_types['shutdown'] = entrypoint

        components = app.create_components()
        assert len(components) == 1
        assert isinstance(components[0], ShutdownComponent)

    def test_nonexistent_component(self, app):
        del app.component_types['shutdown']
        exc = pytest.raises(LookupError, app.create_components)
        assert str(exc.value) == 'no such component: shutdown'

    @pytest.mark.parametrize('shutdown_method', ['stop', 'exit'])
    @pytest.mark.parametrize('logging_config', [
        True,
        {'version': 1, 'loggers': {'asphalt': {'level': 'INFO'}}}
    ], ids=['basic', 'dictconfig'])
    def test_run(self, event_loop, app, caplog, shutdown_method, logging_config):
        """
        Tests that both started and finished callbacks are run when the application is started and
        shut down properly.
        """

        app.component_options['shutdown']['method'] = shutdown_method
        app.logging_config = logging_config
        app.run(event_loop)

        assert app.start_callback_called
        assert app.finish_callback_called
        records = [record for record in caplog.records() if record.name == 'asphalt.core']
        assert len(records) == 4
        assert records[0].message == 'Starting components'
        assert records[1].message == 'All components started'
        assert records[2].message == 'Application started'
        assert records[3].message == 'Application stopped'

    def test_start_exception(self, event_loop, app, caplog):
        """
        Tests that an exception caught during the application initialization is put into the
        application context and made available to finish callbacks.
        """

        def finish(app_ctx):
            nonlocal exception
            exception = app_ctx.exception

        def start(app_ctx: ApplicationContext):
            app_ctx.add_callback(ContextEventType.finished, finish)
            raise Exception('bad component')

        exception = None
        app.start = start
        app.run(event_loop)

        assert str(exception) == 'bad component'
        records = [record for record in caplog.records() if record.name == 'asphalt.core']
        assert len(records) == 4
        assert records[0].message == 'Starting components'
        assert records[1].message == 'All components started'
        assert records[2].message == 'Error during application startup'
        assert records[3].message == 'Application stopped'

    def test_run_baseexception(self, event_loop, app, caplog):
        """
        Tests that BaseExceptions aren't caught anywhere in the stack and crash the application.
        """

        app.component_options['shutdown']['method'] = 'exception'
        exc = pytest.raises(BaseException, app.run, event_loop)
        assert str(exc.value) == 'this should crash the application'

        assert app.start_callback_called
        records = [record for record in caplog.records() if record.name == 'asphalt.core']
        assert len(records) == 3
        assert records[0].message == 'Starting components'
        assert records[1].message == 'All components started'
        assert records[2].message == 'Application started'
