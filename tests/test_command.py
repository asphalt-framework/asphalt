from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from asphalt.core import command
from asphalt.core.component import ContainerComponent


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.parametrize('loop', [None, 'uvloop'], ids=['default', 'override'])
@pytest.mark.parametrize('unsafe', [False, True], ids=['safe', 'unsafe'])
def test_run(runner, unsafe, loop):
    if unsafe:
        component_class = '!!python/name:{0.__module__}.{0.__name__}'.format(ContainerComponent)
    else:
        component_class = '{0.__module__}:{0.__name__}'.format(ContainerComponent)

    config = """\
---
event_loop_policy: bogus
component:
  type: {}
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
        assert len(args) == 1
        assert isinstance(args[0], ContainerComponent)
        assert kwargs == {
            'event_loop_policy': loop or 'bogus',
            'logging': {'version': 1, 'disable_existing_loggers': False}
        }


def test_run_missing_component_key(tmpdir, runner):
    path = tmpdir.join('test.yaml')
    path.write("""\
---
logging:
  version: 1
  disable_existing_loggers: false
""")
    result = runner.invoke(command.run, [str(path)])
    assert str(result.exception) == 'missing configuration key: component'
