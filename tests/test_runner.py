from asyncio import new_event_loop, set_event_loop, get_event_loop, coroutine
import sys

import pytest

from asphalt.core.component import Component
from asphalt.core.context import Context
from asphalt.core.runner import run_application


class ShutdownComponent(Component):
    def __init__(self, method: str):
        self.method = method
        self.start_callback_called = False
        self.finish_callback_called = False

    def start(self, ctx: Context):
        def started_callback(event):
            self.start_callback_called = True
            get_event_loop().call_later(0.1, shutdown_callback)

        def finished_callback(event):
            self.finish_callback_called = True

        def shutdown_callback():
            if self.method == 'stop':
                get_event_loop().stop()
            elif self.method == 'exit':
                sys.exit()
            elif self.method == 'exception':
                raise BaseException('this should crash the application')

        ctx.add_listener('started', started_callback)
        ctx.add_listener('finished', finished_callback)


@pytest.yield_fixture(autouse=True)
def event_loop():
    event_loop = new_event_loop()
    set_event_loop(event_loop)
    yield event_loop
    if event_loop.is_running():
        event_loop.stop()


@pytest.mark.parametrize('shutdown_method', ['stop', 'exit'])
@pytest.mark.parametrize('logging_config', [
    True,
    {'version': 1, 'loggers': {'asphalt': {'level': 'INFO'}}}
], ids=['basic', 'dictconfig'])
def test_run(caplog, shutdown_method, logging_config):
    """
    Tests that both started and finished callbacks are run when the application is started and
    shut down properly.
    """

    component = ShutdownComponent(method=shutdown_method)
    run_application(component, logging=logging_config)

    assert component.start_callback_called
    assert component.finish_callback_called
    records = [record for record in caplog.records() if record.name == 'asphalt.core.runner']
    assert len(records) == 3
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Application started'
    assert records[2].message == 'Application stopped'


def test_run_start_coroutine(caplog):
    """
    Tests that if the component's start() method returns a value other than None, it is waited
    for before proceeding.
    """

    component = ShutdownComponent(method='stop')
    component.start = coroutine(component.start)
    run_application(component)

    assert component.start_callback_called
    assert component.finish_callback_called
    records = [record for record in caplog.records() if record.name == 'asphalt.core.runner']
    assert len(records) == 3
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Application started'
    assert records[2].message == 'Application stopped'


def test_run_start_exception(caplog):
    """
    Tests that an exception caught during the application initialization is put into the
    application context and made available to finish callbacks.
    """

    class DummyComponent(Component):
        def start(self, ctx: Context):
            ctx.add_listener('finished', self.finish)
            raise Exception('bad component')

        def finish(self, event):
            self.exception = event.source.exception

    component = DummyComponent()
    run_application(component)

    assert str(component.exception) == 'bad component'
    records = [record for record in caplog.records() if record.name == 'asphalt.core.runner']
    assert len(records) == 3
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Error during application startup'
    assert records[2].message == 'Application stopped'


def test_run_baseexception(caplog):
    """
    Tests that BaseExceptions aren't caught anywhere in the stack and crash the application.
    """

    component = ShutdownComponent(method='exception')
    exc = pytest.raises(BaseException, run_application, component)
    assert str(exc.value) == 'this should crash the application'

    records = [record for record in caplog.records() if record.name == 'asphalt.core.runner']
    assert len(records) == 2
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Application started'
