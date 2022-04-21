from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from _pytest.monkeypatch import MonkeyPatch
from click.testing import CliRunner

from asphalt.core import Component, Context, _cli


class DummyComponent(Component):
    def __init__(self, dummyval1=None, dummyval2=None):
        self.dummyval1 = dummyval1
        self.dummyval2 = dummyval2

    def start(self, ctx: Context) -> None:
        pass


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.parametrize("loop", [None, "uvloop"], ids=["default", "override"])
def test_run(
    runner: CliRunner,
    loop: str | None,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MYENVVAR", "from environment")
    tmp_path = tmp_path.joinpath("tmpfile")
    tmp_path.write_text("Hello, World!")

    config = f"""\
---
event_loop_policy: bogus
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
    args = ["test.yml"]
    if loop:
        args.extend(["--loop", loop])

    with runner.isolated_filesystem(), patch(
        "asphalt.core._cli.run_application"
    ) as run_app:
        Path("test.yml").write_text(config)
        result = runner.invoke(_cli.run, args)

        assert result.exit_code == 0
        assert run_app.call_count == 1
        args, kwargs = run_app.call_args
        assert len(args) == 0
        assert kwargs == {
            "component": {
                "type": DummyComponent,
                "dummyval1": "testval",
                "envval": "from environment",
                "textfileval": "Hello, World!",
                "binaryfileval": b"Hello, World!",
            },
            "event_loop_policy": loop or "bogus",
            "logging": {"version": 1, "disable_existing_loggers": False},
        }


def test_run_multiple_configs(runner: CliRunner) -> None:
    component_class = "{0.__module__}:{0.__name__}".format(DummyComponent)
    config1 = """\
---
component:
  type: {}
  dummyval1: testval
logging:
  version: 1
  disable_existing_loggers: false
""".format(
        component_class
    )
    config2 = """\
---
component:
  dummyval1: alternate
  dummyval2: 10
"""

    with runner.isolated_filesystem(), patch(
        "asphalt.core._cli.run_application"
    ) as run_app:
        Path("conf1.yml").write_text(config1)
        Path("conf2.yml").write_text(config2)
        result = runner.invoke(_cli.run, ["conf1.yml", "conf2.yml"])

        assert result.exit_code == 0
        assert run_app.call_count == 1
        args, kwargs = run_app.call_args
        assert len(args) == 0
        assert kwargs == {
            "component": {
                "type": component_class,
                "dummyval1": "alternate",
                "dummyval2": 10,
            },
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
        with runner.isolated_filesystem(), patch(
            "asphalt.core._cli.run_application"
        ) as run_app:
            self.write_config()
            result = runner.invoke(_cli.run, ["-s", service, "config.yml"])

            assert result.exit_code == 0
            assert run_app.call_count == 1
            args, kwargs = run_app.call_args
            assert len(args) == 0
            if service == "server":
                assert kwargs == {
                    "max_threads": 30,
                    "component": {
                        "type": "myproject.server.ServerComponent",
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
                    "logging": {"version": 1, "disable_existing_loggers": False},
                }
            else:
                assert kwargs == {
                    "max_threads": 15,
                    "component": {
                        "type": "myproject.client.ClientComponent",
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
                    "logging": {"version": 1, "disable_existing_loggers": False},
                }

    def test_service_not_found(self, runner: CliRunner) -> None:
        with runner.isolated_filesystem(), patch(
            "asphalt.core._cli.run_application"
        ) as run_app:
            self.write_config()
            result = runner.invoke(_cli.run, ["-s", "foobar", "config.yml"])

            assert result.exit_code == 1
            assert run_app.call_count == 0
            assert result.output == "Error: Service 'foobar' has not been defined\n"

    def test_no_service_selected(self, runner: CliRunner) -> None:
        with runner.isolated_filesystem(), patch(
            "asphalt.core._cli.run_application"
        ) as run_app:
            self.write_config()
            result = runner.invoke(_cli.run, ["config.yml"])

            assert result.exit_code == 1
            assert run_app.call_count == 0
            assert result.output == (
                "Error: Multiple services present in configuration file but no default service "
                "has been defined and no service was explicitly selected with -s / --service\n"
            )

    def test_bad_services_type(self, runner: CliRunner) -> None:
        with runner.isolated_filesystem(), patch(
            "asphalt.core._cli.run_application"
        ) as run_app:
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
        with runner.isolated_filesystem(), patch(
            "asphalt.core._cli.run_application"
        ) as run_app:
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

    def test_run_only_service(self, runner) -> None:
        with runner.isolated_filesystem(), patch(
            "asphalt.core._cli.run_application"
        ) as run_app:
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
            assert len(args) == 0
            assert kwargs == {
                "component": {"type": "myproject.client.ClientComponent"},
                "logging": {"version": 1, "disable_existing_loggers": False},
            }

    def test_run_default_service(self, runner: CliRunner) -> None:
        with runner.isolated_filesystem(), patch(
            "asphalt.core._cli.run_application"
        ) as run_app:
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
            assert len(args) == 0
            assert kwargs == {
                "component": {"type": "myproject.server.ServerComponent"},
                "logging": {"version": 1, "disable_existing_loggers": False},
            }

    def test_service_env_variable(
        self, runner: CliRunner, monkeypatch: MonkeyPatch
    ) -> None:
        with runner.isolated_filesystem(), patch(
            "asphalt.core._cli.run_application"
        ) as run_app:
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
            assert len(args) == 0
            assert kwargs == {
                "component": {"type": "myproject.client.ClientComponent"},
                "logging": {"version": 1, "disable_existing_loggers": False},
            }

    def test_service_env_variable_override(
        self, runner: CliRunner, monkeypatch: MonkeyPatch
    ) -> None:
        with runner.isolated_filesystem(), patch(
            "asphalt.core._cli.run_application"
        ) as run_app:
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
            assert len(args) == 0
            assert kwargs == {
                "component": {"type": "myproject.server.ServerComponent"},
                "logging": {"version": 1, "disable_existing_loggers": False},
            }
