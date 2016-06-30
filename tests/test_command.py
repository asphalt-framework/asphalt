from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from asphalt.core import command, Component, Context, qualified_name


class DummyComponent(Component):
    def __init__(self, dummyval1=None, dummyval2=None):
        self.dummyval1 = dummyval1
        self.dummyval2 = dummyval2

    def start(self, ctx: Context):
        pass


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.parametrize('loop', [None, 'uvloop'], ids=['default', 'override'])
@pytest.mark.parametrize('unsafe', [False, True], ids=['safe', 'unsafe'])
def test_run(runner, unsafe, loop):
    if unsafe:
        component_class = '!!python/name:{0.__module__}.{0.__name__}'.format(DummyComponent)
    else:
        component_class = '{0.__module__}:{0.__name__}'.format(DummyComponent)

    config = """\
---
event_loop_policy: bogus
component:
  type: {}
  dummyval1: testval
logging:
  version: 1
  disable_existing_loggers: false
""".format(component_class)
    args = ['test.yml']
    if unsafe:
        args.append('--unsafe')
    if loop:
        args.extend(['--loop', loop])

    with runner.isolated_filesystem(), patch('asphalt.core.command.run_application') as run_app:
        Path('test.yml').write_text(config)
        result = runner.invoke(command.run, args)

        assert result.exit_code == 0
        assert run_app.call_count == 1
        args, kwargs = run_app.call_args
        assert len(args) == 0
        assert kwargs == {
            'component': {
                'type': DummyComponent if unsafe else component_class,
                'dummyval1': 'testval'
            },
            'event_loop_policy': loop or 'bogus',
            'logging': {'version': 1, 'disable_existing_loggers': False}
        }


def test_run_multiple_configs(runner):
    component_class = '{0.__module__}:{0.__name__}'.format(DummyComponent)
    config1 = """\
---
component:
  type: {}
  dummyval1: testval
logging:
  version: 1
  disable_existing_loggers: false
""".format(component_class)
    config2 = """\
---
component:
  dummyval1: alternate
  dummyval2: 10
"""

    with runner.isolated_filesystem(), patch('asphalt.core.command.run_application') as run_app:
        Path('conf1.yml').write_text(config1)
        Path('conf2.yml').write_text(config2)
        result = runner.invoke(command.run, ['conf1.yml', 'conf2.yml'])

        assert result.exit_code == 0
        assert run_app.call_count == 1
        args, kwargs = run_app.call_args
        assert len(args) == 0
        assert kwargs == {
            'component': {'type': component_class, 'dummyval1': 'alternate', 'dummyval2': 10},
            'logging': {'version': 1, 'disable_existing_loggers': False}
        }
