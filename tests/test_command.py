from unittest.mock import patch, Mock

import pytest
import yaml

from asphalt.core import command
from asphalt.core.component import ContainerComponent


def test_quickstart_application(monkeypatch, tmpdir, capsys):
    def mock_input(text):
        if text == 'Project name: ':
            return 'Example Project'
        elif text == 'Top level package name: ':
            return 'example'
        raise ValueError('Unexpected input: ' + text)

    get_distribution = Mock()
    get_distribution('asphalt').parsed_version.public = '1.0.0'
    monkeypatch.setattr('pkg_resources.get_distribution', get_distribution)
    monkeypatch.setattr('builtins.input', mock_input)
    tmpdir.chdir()
    command.quickstart_application()

    # Check that the project directory and the top level package were created
    projectdir = tmpdir.join('Example Project')
    assert projectdir.check(dir=True)
    assert projectdir.join('example').join('__init__.py').check(file=1)

    # Check that example/application.py was properly generated
    with projectdir.join('example').join('application.py').open() as f:
        assert f.read() == """\
from asyncio import coroutine

from asphalt.core.component import ContainerComponent
from asphalt.core.context import Context


class ExampleProjectApplication(ContainerComponent):
    @coroutine
    def start(ctx: Context):
        # Add components and resources here as needed
        yield from super().start(ctx)
        # The components have started now
"""

    with projectdir.join('config.yml').open() as f:
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
    with projectdir.join('setup.py').open() as f:
        assert f.read() == """\
from setuptools import setup

setup(
    name='example',
    version='1.0.0',
    description='Example Project',
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
        'asphalt >= 1.0.0, < 2.0.0'
    ]
)
"""

    # Check that another run will raise an error because the directory exists already
    pytest.raises(SystemExit, command.quickstart_application)
    out, err = capsys.readouterr()
    assert err == 'Error: the directory "Example Project" already exists.\n'


@pytest.mark.parametrize('unsafe', [False, True], ids=['safe', 'unsafe'])
def test_run_from_config_file(tmpdir, unsafe):
    if unsafe:
        component_class = '!!python/name:{0.__module__}.{0.__name__}'.format(ContainerComponent)
    else:
        component_class = '{0.__module__}:{0.__name__}'.format(ContainerComponent)

    path = tmpdir.join('test.yaml')
    path.write("""\
---
runner: test_command:alternate_runner
component:
  type: {}
logging:
  version: 1
  disable_existing_loggers: false
""".format(component_class))

    global alternate_runner
    alternate_runner = Mock()
    try:
        command.run_from_config_file(str(path), unsafe=unsafe)

        assert alternate_runner.call_count == 1
        args, kwargs = alternate_runner.call_args
        assert len(args) == 1
        assert isinstance(args[0], ContainerComponent)
        assert len(kwargs) == 1
        assert kwargs['logging'] == {'version': 1, 'disable_existing_loggers': False}
    finally:
        del alternate_runner


def test_run_from_config_file_missing_component_key(tmpdir):
    path = tmpdir.join('test.yaml')
    path.write("""\
---
logging:
  version: 1
  disable_existing_loggers: false
""")
    exc = pytest.raises(LookupError, command.run_from_config_file, str(path))
    assert str(exc.value) == 'missing configuration key: component'


@pytest.mark.parametrize('args, exits', [
    (['asphalt', '--help'], True),
    (['asphalt'], False)
], ids=['help', 'noargs'])
def test_main_help(capsys, args, exits):
    with patch('sys.argv', args):
        pytest.raises(SystemExit, command.main) if exits else command.main()

    out, err = capsys.readouterr()
    assert out.startswith('usage: asphalt [-h]')


def test_main_run():
    args = ['/bogus/path', '--unsafe']
    patch1 = patch('sys.argv', ['asphalt', 'run'] + args)
    patch2 = patch.object(command, 'run_from_config_file')
    with patch1, patch2 as run_from_config_file:
        command.main()
        assert run_from_config_file.called_once_with(args)
