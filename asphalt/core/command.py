import click
import yaml

from asphalt.core.component import component_types
from asphalt.core.util import PluginContainer

runners = PluginContainer('asphalt.core.runners')


@click.group()
def main():
    pass  # pragma: no cover


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
