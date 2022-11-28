from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import click
from ruamel.yaml import YAML, ScalarNode
from ruamel.yaml.loader import Loader

from asphalt.core.runner import policies, run_application
from asphalt.core.utils import merge_config, qualified_name


def env_constructor(loader: Loader, node: ScalarNode) -> str | None:
    value = loader.construct_scalar(node)
    return os.getenv(value)


def text_file_constructor(loader: Loader, node: ScalarNode) -> str:
    value = loader.construct_scalar(node)
    return Path(value).read_text()


def binary_file_constructor(loader: Loader, node: ScalarNode) -> bytes:
    value = loader.construct_scalar(node)
    return Path(value).read_bytes()


@click.group()
def main() -> None:
    pass  # pragma: no cover


@main.command(help="Read one or more configuration files and start the application.")
@click.argument("configfile", type=click.File(), nargs=-1, required=True)
@click.option(
    "--unsafe",
    is_flag=True,
    default=False,
    help="use unsafe mode when loading YAML (enables markup extensions)",
)
@click.option(
    "-l",
    "--loop",
    type=click.Choice(policies.names),
    help="alternate event loop policy",
)
@click.option(
    "-s",
    "--service",
    type=str,
    help="service to run (if the configuration file contains multiple services)",
)
@click.option(
    "--set",
    "set_",
    multiple=True,
    type=str,
    help="set configuration",
)
def run(
    configfile,
    unsafe: bool,
    loop: Optional[str],
    service: Optional[str],
    set_: str,
) -> None:
    yaml = YAML(typ="unsafe" if unsafe else "safe")
    yaml.constructor.add_constructor("!Env", env_constructor)
    yaml.constructor.add_constructor("!TextFile", text_file_constructor)
    yaml.constructor.add_constructor("!BinaryFile", binary_file_constructor)

    # Read the configuration from the supplied YAML files
    config: Dict[str, Any] = {}
    for path in configfile:
        config_data = yaml.load(path)
        assert isinstance(
            config_data, dict
        ), "the document root element must be a dictionary"
        config = merge_config(config, config_data)

    # Read the configuration from the CLI
    for cli_conf in set_:
        if "=" not in cli_conf:
            click.echo(f"Configuration must be set with '=', got: {cli_conf}")
            raise click.Abort()

        key, value = cli_conf.split("=", 1)
        keys = [k.replace("\\.", ".") for k in re.split("(?<!\\\\)\\.", key)]
        if len(keys) > 1:
            # subcomponent configuration
            last_i = len(keys) - 1
            d = config.setdefault("component", {}).setdefault("components", {})
            for i, k in enumerate(keys):
                if i == last_i:
                    d[k] = value
                else:
                    d = d.setdefault(k, {})
        else:
            # root component configuration
            config["component"][key] = value

    # Override the event loop policy if specified
    if loop:
        config["event_loop_policy"] = loop

    services = config.pop("services", {})
    if not isinstance(services, dict):
        raise click.ClickException(
            f'The "services" key must be a dict, not {qualified_name(services)}'
        )

    # If "component" was defined, use that as the default service if one has not been defined yet
    if "component" in config:
        component = config.pop("component")
        services.setdefault("default", dict(component=component))

    # Try to figure out which service to launch
    service = service or os.getenv("ASPHALT_SERVICE")
    if len(services) == 0:
        raise click.ClickException("No services have been defined")
    elif service:
        try:
            service_config = services[service]
        except KeyError:
            raise click.ClickException(
                f"Service {service!r} has not been defined"
            ) from None
    elif len(services) == 1:
        service_config = next(iter(services.values()))
    elif "default" in services:
        service_config = services["default"]
    else:
        raise click.ClickException(
            "Multiple services present in configuration file but no default service has been "
            "defined and no service was explicitly selected with -s / --service"
        )

    # Merge the service-level configuration with the top level one
    config = merge_config(config, service_config)

    # Start the application
    run_application(**config)
