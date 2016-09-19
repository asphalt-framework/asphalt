import logging
import sys
import asyncio
from unittest.mock import patch

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
        self.exception = event.exception

    async def start(self, ctx: Context):
        ctx.finished.connect(self.finish_callback)

        if self.method == 'stop':
            asyncio.get_event_loop().stop()
        elif self.method == 'exit':
            asyncio.get_event_loop().call_later(0.1, sys.exit)
        elif self.method == 'exception':
            raise RuntimeError('this should crash the application')


@pytest.mark.parametrize('logging_config', [
    None,
    logging.INFO,
    {'version': 1, 'loggers': {'asphalt': {'level': 'INFO'}}}
], ids=['disabled', 'loglevel', 'dictconfig'])
def test_run_logging_config(event_loop, logging_config):
    """Test that logging initialization happens as expected."""
    with patch('asphalt.core.runner.basicConfig') as basicConfig,\
            patch('asphalt.core.runner.dictConfig') as dictConfig:
        run_application(ShutdownComponent(), logging=logging_config)

    assert basicConfig.call_count == (1 if logging_config == logging.INFO else 0)
    assert dictConfig.call_count == (1 if isinstance(logging_config, dict) else 0)


@pytest.mark.parametrize('max_threads', [None, 3])
def test_run_max_threads(event_loop, max_threads):
    """
    Test that a new default executor is installed if and only if the max_threads argument is given.

    """
    component = ShutdownComponent()
    with patch('asphalt.core.runner.ThreadPoolExecutor') as mock_executor:
        run_application(component, max_threads=max_threads)

    if max_threads:
        mock_executor.assert_called_once_with(max_threads)
    else:
        assert not mock_executor.called


@pytest.mark.parametrize('policy, policy_name', [
    ('uvloop', 'uvloop.EventLoopPolicy'),
    ('gevent', 'aiogevent.EventLoopPolicy')
], ids=['uvloop', 'gevent'])
def test_event_loop_policy(caplog, policy, policy_name):
    """Test that a the runner switches to a different event loop policy when instructed to."""
    component = ShutdownComponent()
    run_application(component, event_loop_policy=policy)

    records = [record for record in caplog.records if record.name == 'asphalt.core.runner']
    assert len(records) == 3
    assert records[0].message == 'Switched event loop policy to %s' % policy_name
    assert records[1].message == 'Starting application'
    assert records[2].message == 'Application stopped'


def test_run_callbacks(event_loop, caplog):
    """
    Test that the "finished" callbacks are run when the application is started and shut down
    properly and that the proper logging messages are emitted.

    """
    component = ShutdownComponent()
    run_application(component)

    assert component.finish_callback_called
    records = [record for record in caplog.records if record.name == 'asphalt.core.runner']
    assert len(records) == 2
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Application stopped'


def test_run_sysexit(event_loop, caplog):
    """Test that calling sys.exit() will gracefully shut down the application."""
    component = ShutdownComponent(method='exit')
    run_application(component)

    assert component.finish_callback_called
    records = [record for record in caplog.records if record.name == 'asphalt.core.runner']
    assert len(records) == 2
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Application stopped'


def test_run_start_exception(event_loop, caplog):
    """
    Test that an exception caught during the application initialization is put into the
    application context and made available to finish callbacks.

    """
    component = ShutdownComponent(method='exception')
    pytest.raises(SystemExit, run_application, component)

    assert str(component.exception) == 'this should crash the application'
    records = [record for record in caplog.records if record.name == 'asphalt.core.runner']
    assert len(records) == 3
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Error during application startup'
    assert records[2].message == 'Application stopped'


def test_dict_config(event_loop, caplog):
    """Test that component configuration passed as a dictionary works."""
    component_class = '{0.__module__}:{0.__name__}'.format(ShutdownComponent)
    run_application(component={'type': component_class})

    records = [record for record in caplog.records if record.name == 'asphalt.core.runner']
    assert len(records) == 2
    assert records[0].message == 'Starting application'
    assert records[1].message == 'Application stopped'
