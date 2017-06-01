from typing import Optional, Dict, Any

import click
from ruamel import yaml
from ruamel.yaml import Loader

from asphalt.core.runner import run_application, policies
from asphalt.core.utils import merge_config


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
    config = {}  # type: Dict[str, Any]
    for path in configfile:
        config_data = yaml.load(path, Loader=Loader) if unsafe else yaml.safe_load(path)
        assert isinstance(config_data, dict), 'the document root element must be a dictionary'
        config = merge_config(config, config_data)

    # Override the event loop policy if specified
    if loop:
        config['event_loop_policy'] = loop

    # Start the application
    run_application(**config)
