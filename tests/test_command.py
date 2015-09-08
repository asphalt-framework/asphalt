from unittest.mock import patch, Mock

import pytest
import yaml

from asphalt.core import command

DerivedApplication = None


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

from asphalt.core.application import Application
from asphalt.core.context import Context


class ExampleProjectApplication(Application):
    def __init__(self, **config):
        super().__init__(**config)
        # ADD COMPONENTS HERE

    @coroutine
    def start(ctx: Context):
        # ADD ADDITIONAL RESOURCES HERE (if needed)
        pass
"""

    with projectdir.join('config.yml').open() as f:
        config_data = f.read()
        assert isinstance(yaml.load(config_data), dict)
        assert config_data == """\
---
application: example:ExampleProjectApplication
components:
  foo: {}  # REPLACE ME
settings:
  bar: 1  # REPLACE ME
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
        app_class = '!!python/name:{}.DerivedApplication'.format(__spec__.name)
    else:
        app_class = '{}:DerivedApplication'.format(__spec__.name)

    with patch('{}.DerivedApplication'.format(__spec__.name)) as cls:
        path = tmpdir.join('test.yaml')
        path.write("""\
---
application: {}
components:
    foo: {{}}
    bar: {{}}
settings:
    setting: blah
logging:
  version: 1
  disable_existing_loggers: false
""".format(app_class))
        command.run_from_config_file(str(path), unsafe)

        components = {'foo': {}, 'bar': {}}
        logging = {'version': 1, 'disable_existing_loggers': False}
        settings = {'setting': 'blah'}
        cls.assert_called_once_with(components=components, logging=logging, settings=settings)
        cls().run.assert_called_once_with()


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
