from asyncio import coroutine, new_event_loop, set_event_loop, get_event_loop
from unittest.mock import Mock
import sys

from pkg_resources import EntryPoint
import pytest

from asphalt.core.application import Application
from asphalt.core.component import Component
from asphalt.core.context import Context
from asphalt.core.util import asynchronous


class ShutdownAPI:
    def __init__(self, ctx: Context, method: str):
        self.ctx = ctx
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
        self.ctx.add_listener('started', schedule)


class ShutdownComponent(Component):
    def __init__(self, method: str):
        self.method = method

    def start(self, ctx: Context):
        ctx.add_resource(ShutdownAPI(ctx, self.method), context_var='shutter')


class CustomApp(Application):
    def __init__(self, components=None, **kwargs):
        super().__init__(components, **kwargs)
        self.start_callback_called = False
        self.finish_callback_called = False

    @coroutine
    def start(self, ctx: Context):
        def started_callback(ctx):
            self.start_callback_called = True

        def finished_callback(ctx):
            self.finish_callback_called = True

        yield from super().start(ctx)
        ctx.add_listener('started', started_callback)
        ctx.add_listener('finished', finished_callback)
        ctx.shutter.shutdown()


class TestApplication:
    @pytest.fixture
    def event_loop(self):
        event_loop = new_event_loop()
        set_event_loop(event_loop)
        return event_loop

    @pytest.fixture
    def app(self):
        app = CustomApp()
        app.component_types['shutdown'] = ShutdownComponent
        return app

    @pytest.mark.parametrize('use_entrypoint', [True, False], ids=['entrypoint', 'explicit_class'])
    def test_add_component(self, use_entrypoint):
        """
        Tests that add_component works with an without an entry point and that external
        configuration overriddes local (hardcoded) configuration values.
        """

        app = CustomApp({'shutdown': {'method': 'stop'}})
        if not use_entrypoint:
            app.add_component('shutdown', ShutdownComponent, method='exception')
        else:
            entrypoint = EntryPoint('shutdown', __spec__.name)
            entrypoint.load = Mock(return_value=ShutdownComponent)
            app.component_types['shutdown'] = entrypoint
            app.add_component('shutdown', method='exception')

        assert len(app.components) == 1
        assert isinstance(app.components[0], ShutdownComponent)
        assert app.components[0].method == 'stop'

    @pytest.mark.parametrize('alias, cls, exc_cls, message', [
        ('', None, TypeError, 'component_alias must be a nonempty string'),
        (6, None, TypeError, 'component_alias must be a nonempty string'),
        ('foo', None, LookupError, 'no such component type: foo'),
        ('foo', int, TypeError, 'the component class must be a subclass of asphalt.core.Component')
    ])
    def test_add_component_errors(self, app, alias, cls, exc_cls, message):
        exc = pytest.raises(exc_cls, app.add_component, alias, cls)
        assert str(exc.value) == message

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

        app.add_component('shutdown', method=shutdown_method)
        app.logging_config = logging_config
        app.run()

        assert app.start_callback_called
        assert app.finish_callback_called
        records = [record for record in caplog.records() if record.name == 'asphalt.core']
        assert len(records) == 3
        assert records[0].message == 'Starting application'
        assert records[1].message == 'Application started'
        assert records[2].message == 'Application stopped'

    def test_start_exception(self, event_loop, app, caplog):
        """
        Tests that an exception caught during the application initialization is put into the
        application context and made available to finish callbacks.
        """

        def finish(event):
            nonlocal exception
            exception = event.source.exception

        def start(ctx: Context):
            ctx.add_listener('finished', finish)
            raise Exception('bad component')

        exception = None
        app.start = start
        app.add_component('shutdown', method='stop')
        app.run()

        assert str(exception) == 'bad component'
        records = [record for record in caplog.records() if record.name == 'asphalt.core']
        assert len(records) == 3
        assert records[0].message == 'Starting application'
        assert records[1].message == 'Error during application startup'
        assert records[2].message == 'Application stopped'

    def test_run_baseexception(self, event_loop, app, caplog):
        """
        Tests that BaseExceptions aren't caught anywhere in the stack and crash the application.
        """

        app.add_component('shutdown', method='exception')
        exc = pytest.raises(BaseException, app.run)
        assert str(exc.value) == 'this should crash the application'

        assert app.start_callback_called
        records = [record for record in caplog.records() if record.name == 'asphalt.core']
        assert len(records) == 2
        assert records[0].message == 'Starting application'
        assert records[1].message == 'Application started'
