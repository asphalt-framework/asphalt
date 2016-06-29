from typing import Optional

import click
import yaml

from asphalt.core.component import component_types
from asphalt.core.runner import run_application, policies
from asphalt.core.util import merge_config


@click.group()
def main():
    pass  # pragma: no cover


@main.command(help='Read one or more configuration files and start the application.')
@click.argument('configfile', type=click.File(), nargs=-1, required=True)
@click.option('--unsafe', is_flag=True, default=False,
              help='use unsafe mode when loading YAML (enables markup extensions)')
@click.option('-l', '--loop', type=click.Choice(policies.names),
              help='alternate event loop policy')
def run(configfile, unsafe: bool, loop: Optional[str]):
    # Read the configuration from the supplied YAML files
    config = {}
    for path in configfile:
        config_data = yaml.load(path) if unsafe else yaml.safe_load(path)
        assert isinstance(config_data, dict), 'the document root element must be a dictionary'
        config = merge_config(config, config_data)

    # Override the event loop policy if specified
    if loop:
        config['event_loop_policy'] = loop

    # Instantiate the root component
    try:
        component_config = config.pop('component')
    except KeyError:
        raise LookupError('missing configuration key: component') from None
    else:
        component = component_types.create_object(**component_config)

    # Start the application
    run_application(component, **config)


if __name__ == '__main__':  # pragma: no cover
    main()
