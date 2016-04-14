import sys
from pathlib import Path

import click
import yaml

from asphalt.core.component import component_types
from asphalt.core.util import PluginContainer

runners = PluginContainer('asphalt.core.runners')


@click.group()
def main():
    pass  # pragma: no cover


@main.command()
@click.option('--project', help='Project name', prompt=True)
@click.option('--package', help='Top level package name', prompt=True)
def quickstart(project: str, package: str):
    component_subclass = '{}Application'.format(project.title().replace(' ', ''))
    project_path = Path(project)
    if project_path.exists():
        print('Error: the directory "{}" already exists.'.format(project_path), file=sys.stderr)
        sys.exit(1)

    top_package = project_path / package
    top_package.mkdir(parents=True)
    with (top_package / '__init__.py').open('w') as f:
        pass

    with (top_package / 'application.py').open('w') as f:
        f.write("""\
from asphalt.core import ContainerComponent, Context


class {component_subclass}(ContainerComponent):
    async def start(ctx: Context):
        # Add components and resources here as needed
        await super().start(ctx)
        # The components have started now
""".format(component_subclass=component_subclass))

    with (project_path / 'config.yml').open('w') as f:
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

    with (project_path / 'setup.py').open('w') as f:
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
        'asphalt'
    ]
)
""".format(package=package, project_name=project))


@main.command(help='Read a configuration file and start the application.')
@click.argument('config', type=click.File())
@click.option('--unsafe', is_flag=True,
              help='use unsafe mode when loading YAML (enables markup extensions)')
def run(config, unsafe: bool=False):
    # Read the configuration from the supplied YAML file
    config_data = yaml.load(config) if unsafe else yaml.safe_load(config)
    assert isinstance(config_data, dict), 'the document root element must be a dictionary'

    # Get a reference to the runner
    runner = runners.resolve(config_data.pop('runner', 'asyncio'))

    # Instantiate the root component
    try:
        component_config = config_data.pop('component')
    except KeyError:
        raise LookupError('missing configuration key: component') from None
    else:
        component = component_types.create_object(**component_config)

    # Start the application
    runner(component, **config_data)


if __name__ == '__main__':  # pragma: no cover
    main()
