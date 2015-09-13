from asyncio import new_event_loop, set_event_loop, get_event_loop, coroutine
import sys

import pytest

from asphalt.core.component import Component
from asphalt.core.context import Context
from asphalt.core.runner import run_application


class ShutdownComponent(Component):
    def __init__(self, method: str='stop'):
        self.method = method
        self.finish_callback_called = False
        self.exception = None

    def finish_callback(self, event):
        self.finish_callback_called = True
        self.exception = event.source.exception

    def start(self, ctx: Context):
        ctx.add_listener('finished', self.finish_callback)

        if self.method == 'stop':
            get_event_loop().stop()
        elif self.method == 'exit':
            get_event_loop().call_later(0.1, sys.exit)
        elif self.method == 'exception':
            raise RuntimeError('this should crash the application')


@pytest.yield_fixture(autouse=True)
def event_loop():
    event_loop = new_event_loop()
    set_event_loop(event_loop)
    yield event_loop
    if event_loop.is_running():
        event_loop.stop()


@pytest.mark.parametrize('coroutine_start', [False, True], ids=['coroutine', 'normal'])
@pytest.mark.parametrize('logging_config', [
    True,
    {'version': 1, 'loggers': {'asphalt': {'level': 'INFO'}}}
], ids=['basic', 'dictconfig'])
def test_run(coroutine_start, caplog, logging_config):
    """
    Tests that the "finished" callbacks are run when the application is started and shut down
    properly and that the proper logging messages are emitted.
    """

    component = ShutdownComponent()
    component.start = coroutine(component.start) if coroutine_start else component.start
    run_application(component, logging=logging_config)

    assert component.finish_callback_called
    records = [record for record in caplog.records() if record.name == 'asphalt.core.runner']
    assert len(records) == 3
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Application started'
    assert records[2].message == 'Application stopped'


def test_run_sysexit(caplog):
    component = ShutdownComponent(method='exit')
    run_application(component)

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

    component = ShutdownComponent(method='exception')
    exc = pytest.raises(RuntimeError, run_application, component)

    assert exc.value == component.exception
    assert str(component.exception) == 'this should crash the application'
    records = [record for record in caplog.records() if record.name == 'asphalt.core.runner']
    assert len(records) == 2
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Error during application startup'
