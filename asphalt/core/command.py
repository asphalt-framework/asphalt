from pathlib import Path
from typing import Union
import argparse
import sys

import pkg_resources
import yaml

from .util import PluginContainer
from .component import ContainerComponent, component_types
from .context import Context

__all__ = 'runners', 'quickstart_application', 'run_from_config_file'

runners = PluginContainer('asphalt.core.runners')


def quickstart_application():
    """Asks a few questions and builds a skeleton application directory structure."""

    current_version = pkg_resources.get_distribution('asphalt').parsed_version.public
    next_major_version = '{}.0.0'.format(int(current_version.split('.')[0]) + 1)

    project_name = input('Project name: ')
    package = input('Top level package name: ')
    component_subclass = '{}Application'.format(
        ''.join(part.capitalize() for part in project_name.split()))

    project = Path(project_name)
    if project.exists():
        print('Error: the directory "{}" already exists.'.format(project), file=sys.stderr)
        sys.exit(1)

    top_package = project / package
    top_package.mkdir(parents=True)
    with (top_package / '__init__.py').open('w') as f:
        pass

    with (top_package / 'application.py').open('w') as f:
        f.write("""\
from asyncio import coroutine

from {component_cls.__module__} import {component_cls.__name__}
from {context_cls.__module__} import {context_cls.__name__}


class {component_subclass}({component_cls.__name__}):
    @coroutine
    def start(ctx: Context):
        # Add components and resources here as needed
        yield from super().start(ctx)
        # The components have started now
""".format(component_cls=ContainerComponent, context_cls=Context,
           component_subclass=component_subclass))

    with (project / 'config.yml').open('w') as f:
        f.write("""\
---
component:
  type: {package}:{component_subclass}
  components: {{}}  # override component configurations here (or just remove this)
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
""".format(package=package, component_subclass=component_subclass))

    with (project / 'setup.py').open('w') as f:
        f.write("""\
from setuptools import setup

setup(
    name='{package}',
    version='1.0.0',
    description='{project_name}',
    long_description='FILL IN HERE',
    author='FILL IN HERE',
    author_email='FILL IN HERE',
    classifiers=[
        'Programming Language :: Python',
        'Programming Language :: Python :: 3'
    ],
    zip_safe=False,
    packages=[
        '{package}'
    ],
    install_requires=[
        'asphalt >= {current_version}, < {next_major_version}'
    ]
)
""".format(package=package, project_name=project_name, current_version=current_version,
           next_major_version=next_major_version))


def run_from_config_file(config_file: Union[str, Path], runner: str='asyncio', unsafe: bool=False):
    """
    Runs an application using configuration from the given configuration file.

    :param config_file: path to a YAML configuration file
    :param unsafe: ``True`` to load the YAML file in unsafe mode
    """

    # Read the configuration from the supplied YAML file
    with Path(config_file).open() as stream:
        config = yaml.load(stream) if unsafe else yaml.safe_load(stream)
    assert isinstance(config, dict), 'the YAML document root must be a dictionary'

    # Instantiate the top level component
    try:
        component_config = config.pop('component')
    except KeyError:
        raise LookupError('missing configuration key: component') from None
    else:
        component = component_types.create_object(**component_config)

    # Get a reference to the runner
    runner = runners.resolve(config.pop('runner', runner))

    # Start the application
    runner(component, **config)


def main():
    parser = argparse.ArgumentParser(description='The Asphalt application framework')
    subparsers = parser.add_subparsers()

    run_parser = subparsers.add_parser(
        'run', help='Run an Asphalt application from a YAML configuration file')
    run_parser.add_argument('config_file', help='Path to the configuration file')
    run_parser.add_argument(
        '--unsafe', action='store_true', default=False,
        help='Load the YAML file in unsafe mode (enables YAML markup extensions)')
    run_parser.add_argument(
        '--runner', default='asyncio',
        help='Select the runner with which to start the application')
    run_parser.set_defaults(func=run_from_config_file)

    quickstart_parser = subparsers.add_parser(
        'quickstart', help='Quickstart an Asphalt application'
    )
    quickstart_parser.set_defaults(func=quickstart_application)

    args = parser.parse_args(sys.argv[1:])
    if 'func' in args:
        kwargs = dict(args._get_kwargs())
        del kwargs['func']
        args.func(**kwargs)
    else:
        parser.print_help()

if __name__ == '__main__':  # pragma: no cover
    main()
