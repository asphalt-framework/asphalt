from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from asphalt.core import command
from asphalt.core.component import ContainerComponent

mock_runner = None


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.parametrize('unsafe', [False, True], ids=['safe', 'unsafe'])
def test_run(runner, unsafe):
    if unsafe:
        component_class = '!!python/name:{0.__module__}.{0.__name__}'.format(ContainerComponent)
    else:
        component_class = '{0.__module__}:{0.__name__}'.format(ContainerComponent)

    config = """\
---
runner: test_command:mock_runner
component:
  type: {}
logging:
  version: 1
  disable_existing_loggers: false
""".format(component_class)
    args = ['test.yml']
    if unsafe:
        args.append('--unsafe')

    with runner.isolated_filesystem(), patch('test_command.mock_runner') as mock_runner:
        Path('test.yml').write_text(config)
        result = runner.invoke(command.run, args)

        assert result.exit_code == 0
        assert mock_runner.call_count == 1
        args, kwargs = mock_runner.call_args
        assert isinstance(args[0], ContainerComponent)
        assert len(kwargs) == 1
        assert kwargs['logging'] == {'version': 1, 'disable_existing_loggers': False}


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
