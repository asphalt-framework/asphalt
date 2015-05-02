from pathlib import Path
from typing import Union
import argparse
import sys

import pkg_resources
import yaml

from .application import Application
from .context import ApplicationContext
from .util import resolve_reference


def quickstart_application():
    """Asks a few questions and builds a skeleton application directory structure."""

    current_version = pkg_resources.get_distribution('asphalt').parsed_version.public
    next_major_version = '{}.0.0'.format(int(current_version.split('.')[0]) + 1)

    project_name = input('Project name: ')
    package = input('Top level package name: ')
    app_subclass = '{}Application'.format(
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
from {app_cls.__module__} import {app_cls.__name__}
from {context_cls.__module__} import {context_cls.__name__}


class {app_subclass}({app_cls.__name__}):
    @coroutine
    def start(app_ctx: ApplicationContext):
        pass  # IMPLEMENT CUSTOM LOGIC HERE
""".format(app_cls=Application, context_cls=ApplicationContext, app_subclass=app_subclass))

    with (project / 'config.yml').open('w') as f:
        f.write("""\
---
application_class: {package}:{app_subclass}
components:
    foo: {{}}  # REPLACE ME
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
""".format(package=package, app_subclass=app_subclass))

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
        'Intended Audience :: End Users/Desktop',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3'
    ],
    zip_safe=True,
    packages=[
        '{package}'
    ],
    install_requires=[
        'asphalt >= {current_version}, < {next_major_version}'
    ]
)
""".format(package=package, project_name=project_name, current_version=current_version,
           next_major_version=next_major_version))


def run_from_config_file(config_file: Union[str, Path], unsafe: bool=False):
    """
    Runs an application using configuration from the given configuration file.

    :param config_file: path to a YAML configuration file
    :param unsafe: ``True`` to load the YAML file in unsafe mode
    """

    # Read the configuration from the supplied YAML file
    with Path(config_file).open() as stream:
        config = yaml.load(stream) if unsafe else yaml.safe_load(stream)
    assert isinstance(config, dict), 'the YAML document root must be a dictionary'

    # Instantiate and run the application
    application_class = resolve_reference(config.pop('application_class', Application))
    application = application_class(**config)
    application.run()


def main():
    parser = argparse.ArgumentParser(description='The Asphalt application framework')
    subparsers = parser.add_subparsers()

    run_parser = subparsers.add_parser(
        'run', help='Run an Asphalt application from a YAML configuration file')
    run_parser.add_argument('config_file', help='Path to the configuration file')
    run_parser.add_argument(
        '--unsafe', action='store_true', default=False,
        help='Load the YAML file in unsafe mode (enables YAML markup extensions)')
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
