from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from asphalt.core import command
from asphalt.core.component import ContainerComponent

mock_runner = None


@pytest.fixture
def runner():
    return CliRunner()


def test_quickstart_application(runner):
    project = 'Example project'
    package = 'example'
    args = ['--project', project, '--package', package]

    with runner.isolated_filesystem():
        result = runner.invoke(command.quickstart, args)
        assert result.exit_code == 0

        # Check that the project directory and the top level package were created
        projectdir = Path(project)
        pkgpath = projectdir / package
        assert projectdir.is_dir()
        assert pkgpath.joinpath('__init__.py').is_file()

        # Check that example/application.py was properly generated
        with pkgpath.joinpath('application.py').open() as f:
            assert f.read() == """\
from asphalt.core.component import ContainerComponent
from asphalt.core.context import Context


class ExampleProjectApplication(ContainerComponent):
    async def start(ctx: Context):
        # Add components and resources here as needed
        await super().start(ctx)
        # The components have started now
"""

        with projectdir.joinpath('config.yml').open() as f:
            config_data = f.read()
            assert isinstance(yaml.load(config_data), dict)
            assert config_data == """\
---
component:
  type: example:ExampleProjectApplication
  components: {}  # override component configurations here (or just remove this)
logging:
  version: 1
  disable_existing_loggers: false
  handlers:
    console:
      class: logging.StreamHandler
      formatter: generic
  formatters:
    generic:
        format: "%(asctime)s:%(levelname)s:%(name)s:%(message)s"
  root:
    handlers: [console]
    level: INFO
"""

        # Check that setup.py was properly generated
        with projectdir.joinpath('setup.py').open() as f:
            assert f.read() == """\
from setuptools import setup

setup(
    name='example',
    version='1.0.0',
    description='Example project',
    long_description='FILL IN HERE',
    author='FILL IN HERE',
    author_email='FILL IN HERE',
    classifiers=[
        'Programming Language :: Python',
        'Programming Language :: Python :: 3'
    ],
    zip_safe=False,
    packages=[
        'example'
    ],
    install_requires=[
        'asphalt'
    ]
)
"""

        # Check that another run will raise an error because the directory exists already
        result = runner.invoke(command.quickstart, args)
        assert result.exit_code == 1
        assert result.output == 'Error: the directory "Example project" already exists.\n'


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
