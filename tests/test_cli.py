from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from pytest import MonkeyPatch

from asphalt.core import CLIApplicationComponent, _cli

pytestmark = pytest.mark.anyio()


class DummyComponent(CLIApplicationComponent):
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def run(self) -> None:
        def convert_binary(val: Any) -> Any:
            if isinstance(val, bytes):
                return repr(val)

            return val

        print(json.dumps(self.kwargs, default=convert_binary))


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_run(
    runner: CliRunner,
    anyio_backend_name: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MYENVVAR", "from environment")
    tmp_path = tmp_path.joinpath("tmpfile")
    tmp_path.write_text("Hello, World!")

    config = f"""\
---
backend: {anyio_backend_name}
component:
  type: !!python/name:{DummyComponent.__module__}.{DummyComponent.__name__}
  dummyval1: testval
  envval: !Env MYENVVAR
  textfileval: !TextFile {tmp_path}
  binaryfileval: !BinaryFile {tmp_path}
logging:
  version: 1
  disable_existing_loggers: false
"""
    with runner.isolated_filesystem():
        Path("test.yml").write_text(config)
        result = runner.invoke(_cli.run, ["test.yml"])

        assert result.exit_code == 0
        kwargs = json.loads(result.stdout)
        assert kwargs == {
            "dummyval1": "testval",
            "envval": "from environment",
            "textfileval": "Hello, World!",
            "binaryfileval": "b'Hello, World!'",
        }


def test_run_bad_override(runner: CliRunner) -> None:
    config = """\
    component:
        type: does.not.exist:Component
"""
    with runner.isolated_filesystem():
        Path("test.yml").write_text(config)
        result = runner.invoke(_cli.run, ["test.yml", "--set", "foobar"])
        assert result.exit_code == 1
        assert result.stdout == (
            "Error: Configuration must be set with '=', got: foobar\n"
        )


def test_run_missing_root_component_config(runner: CliRunner) -> None:
    config = """\
        services:
            default:
    """
    with runner.isolated_filesystem():
        Path("test.yml").write_text(config)
        result = runner.invoke(_cli.run, ["test.yml"])
        assert result.exit_code == 1
        assert result.stdout == (
            "Error: Service configuration is missing the 'component' key\n"
        )


def test_run_missing_root_component_type(runner: CliRunner) -> None:
    config = """\
        services:
            default:
                component: {}
    """
    with runner.isolated_filesystem():
        Path("test.yml").write_text(config)
        result = runner.invoke(_cli.run, ["test.yml"])
        assert result.exit_code == 1
        assert result.stdout == (
            "Error: Root component configuration is missing the 'type' key\n"
        )


def test_run_bad_path(runner: CliRunner) -> None:
    config = """\
    component:
        type: does.not.exist:Component
        listvalue: []
"""
    with runner.isolated_filesystem():
        Path("test.yml").write_text(config)
        result = runner.invoke(
            _cli.run, ["test.yml", "--set", "component.listvalue.foo=1"]
        )
        assert result.exit_code == 1
        assert result.stdout == (
            "Error: Cannot apply override for 'component.listvalue.foo': value at "
            "component ⟶ listvalue is not a mapping, but list\n"
        )


def test_run_multiple_configs(runner: CliRunner) -> None:
    component_class = f"{DummyComponent.__module__}:{DummyComponent.__name__}"
    config1 = f"""\
---
component:
  type: {component_class}
  dummyval1: testval
logging:
  version: 1
  disable_existing_loggers: false
"""
    config2 = """\
---
component:
  dummyval1: alternate
  dummyval2: 10
  dummyval3: foo
"""

    with (
        runner.isolated_filesystem(),
        patch("asphalt.core._cli.run_application") as run_app,
    ):
        Path("conf1.yml").write_text(config1)
        Path("conf2.yml").write_text(config2)
        result = runner.invoke(
            _cli.run,
            [
                "conf1.yml",
                "conf2.yml",
                "--set",
                "component.dummyval3=bar",
                "--set",
                "component.dummyval4=baz",
            ],
        )

        assert result.exit_code == 0
        assert run_app.call_count == 1
        args, kwargs = run_app.call_args
        assert args == (
            component_class,
            {
                "dummyval1": "alternate",
                "dummyval2": 10,
                "dummyval3": "bar",
                "dummyval4": "baz",
            },
        )
        assert kwargs == {
            "backend": "asyncio",
            "backend_options": {},
            "logging": {"version": 1, "disable_existing_loggers": False},
        }


class TestServices:
    def write_config(self) -> None:
        Path("config.yml").write_text(
            """\
---
max_threads: 15
services:
  server:
    max_threads: 30
    component:
      type: myproject.server.ServerComponent
      components:
        wamp: &wamp
          host: wamp.example.org
          port: 8000
          tls: true
          auth_id: serveruser
          auth_secret: serverpass
        mailer:
          backend: smtp
  client:
    component:
      type: myproject.client.ClientComponent
      components:
        wamp:
          <<: *wamp
          auth_id: clientuser
          auth_secret: clientpass
logging:
  version: 1
  disable_existing_loggers: false
"""
        )

    @pytest.mark.parametrize("service", ["server", "client"])
    def test_run_service(self, runner: CliRunner, service: str) -> None:
        with (
            runner.isolated_filesystem(),
            patch("asphalt.core._cli.run_application") as run_app,
        ):
            self.write_config()
            result = runner.invoke(_cli.run, ["-s", service, "config.yml"])

            assert result.exit_code == 0
            assert run_app.call_count == 1
            args, kwargs = run_app.call_args
            if service == "server":
                assert args == (
                    "myproject.server.ServerComponent",
                    {
                        "components": {
                            "wamp": {
                                "host": "wamp.example.org",
                                "port": 8000,
                                "tls": True,
                                "auth_id": "serveruser",
                                "auth_secret": "serverpass",
                            },
                            "mailer": {"backend": "smtp"},
                        },
                    },
                )
                assert kwargs == {
                    "backend": "asyncio",
                    "backend_options": {},
                    "max_threads": 30,
                    "logging": {"version": 1, "disable_existing_loggers": False},
                }
            else:
                assert args == (
                    "myproject.client.ClientComponent",
                    {
                        "components": {
                            "wamp": {
                                "host": "wamp.example.org",
                                "port": 8000,
                                "tls": True,
                                "auth_id": "clientuser",
                                "auth_secret": "clientpass",
                            }
                        },
                    },
                )
                assert kwargs == {
                    "backend": "asyncio",
                    "backend_options": {},
                    "max_threads": 15,
                    "logging": {"version": 1, "disable_existing_loggers": False},
                }

    def test_service_not_found(self, runner: CliRunner) -> None:
        with (
            runner.isolated_filesystem(),
            patch("asphalt.core._cli.run_application") as run_app,
        ):
            self.write_config()
            result = runner.invoke(_cli.run, ["-s", "foobar", "config.yml"])

            assert result.exit_code == 1
            assert run_app.call_count == 0
            assert result.output == "Error: Service 'foobar' has not been defined\n"

    def test_no_service_selected(self, runner: CliRunner) -> None:
        with (
            runner.isolated_filesystem(),
            patch("asphalt.core._cli.run_application") as run_app,
        ):
            self.write_config()
            result = runner.invoke(_cli.run, ["config.yml"])

            assert result.exit_code == 1
            assert run_app.call_count == 0
            assert result.output == (
                "Error: Multiple services present in configuration file but no "
                "default service has been defined and no service was explicitly "
                "selected with -s / --service\n"
            )

    def test_bad_services_type(self, runner: CliRunner) -> None:
        with (
            runner.isolated_filesystem(),
            patch("asphalt.core._cli.run_application") as run_app,
        ):
            Path("config.yml").write_text(
                """\
---
services: blah
logging:
  version: 1
  disable_existing_loggers: false
"""
            )
            result = runner.invoke(_cli.run, ["config.yml"])

            assert result.exit_code == 1
            assert run_app.call_count == 0
            assert (
                result.output == 'Error: The "services" key must be a dict, not str\n'
            )

    def test_no_services_defined(self, runner: CliRunner) -> None:
        with (
            runner.isolated_filesystem(),
            patch("asphalt.core._cli.run_application") as run_app,
        ):
            Path("config.yml").write_text(
                """\
---
services: {}
logging:
  version: 1
  disable_existing_loggers: false
"""
            )
            result = runner.invoke(_cli.run, ["config.yml"])

            assert result.exit_code == 1
            assert run_app.call_count == 0
            assert result.output == "Error: No services have been defined\n"

    def test_run_only_service(self, runner: CliRunner) -> None:
        with (
            runner.isolated_filesystem(),
            patch("asphalt.core._cli.run_application") as run_app,
        ):
            Path("config.yml").write_text(
                """\
---
services:
  whatever:
    component:
      type: myproject.client.ClientComponent
logging:
  version: 1
  disable_existing_loggers: false
"""
            )
            result = runner.invoke(_cli.run, ["config.yml"])

            assert result.exit_code == 0
            assert run_app.call_count == 1
            args, kwargs = run_app.call_args
            assert args == ("myproject.client.ClientComponent", {})
            assert kwargs == {
                "backend": "asyncio",
                "backend_options": {},
                "logging": {"version": 1, "disable_existing_loggers": False},
            }

    def test_run_default_service(self, runner: CliRunner) -> None:
        with (
            runner.isolated_filesystem(),
            patch("asphalt.core._cli.run_application") as run_app,
        ):
            Path("config.yml").write_text(
                """\
---
services:
  whatever:
    component:
      type: myproject.client.ClientComponent
  default:
    component:
      type: myproject.server.ServerComponent
logging:
  version: 1
  disable_existing_loggers: false
"""
            )
            result = runner.invoke(_cli.run, ["config.yml"])

            assert result.exit_code == 0
            assert run_app.call_count == 1
            args, kwargs = run_app.call_args
            assert args == ("myproject.server.ServerComponent", {})
            assert kwargs == {
                "backend": "asyncio",
                "backend_options": {},
                "logging": {"version": 1, "disable_existing_loggers": False},
            }

    def test_service_env_variable(
        self, runner: CliRunner, monkeypatch: MonkeyPatch
    ) -> None:
        with (
            runner.isolated_filesystem(),
            patch("asphalt.core._cli.run_application") as run_app,
        ):
            Path("config.yml").write_text(
                """\
---
services:
  whatever:
    component:
      type: myproject.client.ClientComponent
  default:
    component:
      type: myproject.server.ServerComponent
logging:
  version: 1
  disable_existing_loggers: false
"""
            )
            monkeypatch.setenv("ASPHALT_SERVICE", "whatever")
            result = runner.invoke(_cli.run, ["config.yml"])

            assert result.exit_code == 0
            assert run_app.call_count == 1
            args, kwargs = run_app.call_args
            assert args == ("myproject.client.ClientComponent", {})
            assert kwargs == {
                "backend": "asyncio",
                "backend_options": {},
                "logging": {"version": 1, "disable_existing_loggers": False},
            }

    def test_service_env_variable_override(
        self, runner: CliRunner, monkeypatch: MonkeyPatch
    ) -> None:
        with (
            runner.isolated_filesystem(),
            patch("asphalt.core._cli.run_application") as run_app,
        ):
            Path("config.yml").write_text(
                """\
---
services:
  whatever:
    component:
      type: myproject.client.ClientComponent
  default:
    component:
      type: myproject.server.ServerComponent
logging:
  version: 1
  disable_existing_loggers: false
"""
            )
            monkeypatch.setenv("ASPHALT_SERVICE", "whatever")
            result = runner.invoke(_cli.run, ["-s", "default", "config.yml"])

            assert result.exit_code == 0
            assert run_app.call_count == 1
            args, kwargs = run_app.call_args
            assert args == ("myproject.server.ServerComponent", {})
            assert kwargs == {
                "backend": "asyncio",
                "backend_options": {},
                "logging": {"version": 1, "disable_existing_loggers": False},
            }
