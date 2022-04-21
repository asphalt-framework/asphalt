from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import anyio
import click
from ruamel.yaml import YAML, ScalarNode
from ruamel.yaml.loader import Loader

from ._runner import run_application
from ._utils import merge_config, qualified_name


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
    "-b",
    "--backend",
    type=click.Choice(anyio.get_all_backends()),
    help="AnyIO backend to use",
)
@click.option(
    "-s",
    "--service",
    type=str,
    help="service to run (if the configuration file contains multiple services)",
)
def run(configfile, backend: str | None, service: str | None) -> None:
    yaml = YAML(typ="unsafe")
    yaml.constructor.add_constructor("!Env", env_constructor)
    yaml.constructor.add_constructor("!TextFile", text_file_constructor)
    yaml.constructor.add_constructor("!BinaryFile", binary_file_constructor)

    # Read the configuration from the supplied YAML files
    config: dict[str, Any] = {}
    for path in configfile:
        config_data = yaml.load(path)
        assert isinstance(
            config_data, dict
        ), "the document root element must be a dictionary"
        config = merge_config(config, config_data)

    services = config.pop("services", {})
    if not isinstance(services, dict):
        raise click.ClickException(
            f'The "services" key must be a dict, not {qualified_name(services)}'
        )

    # If "component" was defined, use that as the default service if one has not been
    # defined yet
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
            "Multiple services present in configuration file but no default service "
            "has been defined and no service was explicitly selected with -s / "
            "--service"
        )

    # Merge the service-level configuration with the top level one
    config = merge_config(config, service_config)

    # Start the application
    backend = backend or config.pop("backend", "asyncio")
    backend_options = config.pop("backend_options", {})
    anyio.run(
        lambda: run_application(**config),
        backend=backend,
        backend_options=backend_options,
    )
