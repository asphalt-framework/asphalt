from __future__ import annotations

import logging
import platform
import re
import signal
from typing import Any, Literal
from unittest.mock import patch

import anyio
import pytest
from _pytest.logging import LogCaptureFixture
from anyio import sleep, to_thread
from anyio.lowlevel import checkpoint

from asphalt.core import (
    CLIApplicationComponent,
    Component,
    add_teardown_callback,
    get_resource,
    run_application,
    start_service_task,
)

pytestmark = pytest.mark.anyio()
windows_signal_mark = pytest.mark.skipif(
    platform.system() == "Windows", reason="Signals don't work on Windows"
)


class ShutdownComponent(Component):
    def __init__(self, method: Literal["keyboard", "sigterm", "exception"]):
        self.method = method
        self.teardown_callback_called = False

    async def stop_app(self) -> None:
        await checkpoint()
        if self.method == "keyboard":
            signal.raise_signal(signal.SIGINT)
        elif self.method == "sigterm":
            signal.raise_signal(signal.SIGTERM)
        elif self.method == "exception":
            raise RuntimeError("this should crash the application")

    async def start(self) -> None:
        await start_service_task(self.stop_app, "Application terminator")


class CrashComponent(Component):
    def __init__(self, method: str = "exit"):
        self.method = method

    async def start(self) -> None:
        if self.method == "keyboard":
            signal.raise_signal(signal.SIGINT)
            await sleep(3)
        elif self.method == "sigterm":
            signal.raise_signal(signal.SIGTERM)
            await sleep(3)
        elif self.method == "exception":
            raise RuntimeError("this should crash the application")


class DummyCLIApp(CLIApplicationComponent):
    def __init__(self, exit_code: int | None = None):
        super().__init__()
        self.exit_code = exit_code
        self.teardown_callback_called = False
        self.exception: BaseException | None = None

    def teardown_callback(self, exception: BaseException | None) -> None:
        self.teardown_callback_called = True
        self.exception = exception

    async def start(self) -> None:
        add_teardown_callback(self.teardown_callback, pass_exception=True)

    async def run(self) -> int | None:
        return self.exit_code


@pytest.mark.parametrize(
    "logging_config",
    [
        pytest.param(None, id="disabled"),
        pytest.param(logging.INFO, id="loglevel"),
        pytest.param(
            {"version": 1, "loggers": {"asphalt": {"level": "INFO"}}}, id="dictconfig"
        ),
    ],
)
def test_run_logging_config(
    logging_config: dict[str, Any] | int | None, anyio_backend_name: str
) -> None:
    """Test that logging initialization happens as expected."""
    with patch("asphalt.core._runner.basicConfig") as basicConfig, patch(
        "asphalt.core._runner.dictConfig"
    ) as dictConfig:
        run_application(
            DummyCLIApp(), logging=logging_config, backend=anyio_backend_name
        )

    assert basicConfig.call_count == (1 if logging_config == logging.INFO else 0)
    assert dictConfig.call_count == (1 if isinstance(logging_config, dict) else 0)


@pytest.mark.parametrize("max_threads", [None, 3])
def test_run_max_threads(max_threads: int | None, anyio_backend_name: str) -> None:
    """
    Test that a new default executor is installed if and only if the max_threads
    argument is given.

    """
    observed_total_tokens: float | None = None

    class MaxThreadsComponent(CLIApplicationComponent):
        async def run(self) -> int | None:
            nonlocal observed_total_tokens
            limiter = to_thread.current_default_thread_limiter()
            observed_total_tokens = limiter.total_tokens
            return None

    async def get_default_total_tokens() -> float:
        limiter = to_thread.current_default_thread_limiter()
        return limiter.total_tokens

    component = MaxThreadsComponent()
    expected_total_tokens = max_threads or anyio.run(
        get_default_total_tokens, backend=anyio_backend_name
    )
    run_application(component, max_threads=max_threads, backend=anyio_backend_name)
    assert observed_total_tokens == expected_total_tokens


def test_run_callbacks(caplog: LogCaptureFixture, anyio_backend_name: str) -> None:
    """
    Test that the teardown callbacks are run when the application is started and shut
    down properly and that the proper logging messages are emitted.

    """
    caplog.set_level(logging.INFO)
    component = DummyCLIApp()
    run_application(component, backend=anyio_backend_name)

    assert component.teardown_callback_called
    records = [
        record for record in caplog.records if record.name == "asphalt.core._runner"
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message == "Application started"
    assert records[3].message == "Application stopped"


@pytest.mark.parametrize(
    "method, expected_stop_message",
    [
        pytest.param(
            "keyboard",
            "Received signal (Interrupt) – terminating application",
            id="keyboard",
            marks=[windows_signal_mark],
        ),
        pytest.param(
            "sigterm",
            "Received signal (Terminated) – terminating application",
            id="sigterm",
            marks=[windows_signal_mark],
        ),
    ],
)
def test_clean_exit(
    caplog: LogCaptureFixture,
    method: Literal["keyboard", "sigterm"],
    expected_stop_message: str | None,
    anyio_backend_name: str,
) -> None:
    """
    Test that when application termination is explicitly requested either externally or
    directly from a service task, it exits cleanly.

    """
    caplog.set_level(logging.INFO)
    component = ShutdownComponent(method=method)
    run_application(component, backend=anyio_backend_name)

    records = [
        record for record in caplog.records if record.name == "asphalt.core._runner"
    ]
    assert len(records) == 5 if expected_stop_message else 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message == "Application started"
    assert records[-1].message == "Application stopped"

    if expected_stop_message:
        assert records[3].message == expected_stop_message


@pytest.mark.parametrize(
    "method, expected_stop_message",
    [
        pytest.param(
            "exception",
            "Error during application startup",
            id="exception",
        ),
        pytest.param(
            "keyboard",
            "Received signal (Interrupt) – terminating application",
            id="keyboard",
            marks=[windows_signal_mark],
        ),
        pytest.param(
            "sigterm",
            "Received signal (Terminated) – terminating application",
            id="sigterm",
            marks=[windows_signal_mark],
        ),
    ],
)
def test_start_exception(
    caplog: LogCaptureFixture,
    anyio_backend_name: str,
    method: str,
    expected_stop_message: str,
) -> None:
    """
    Test that an exception caught during the application initialization is put into the
    application context and made available to teardown callbacks.

    """
    caplog.set_level(logging.INFO)
    component = CrashComponent(method=method)
    with pytest.raises(SystemExit) as exc_info:
        run_application(component, backend=anyio_backend_name)

    assert exc_info.value.code == 1
    records = [
        record for record in caplog.records if record.name.startswith("asphalt.core.")
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message == expected_stop_message
    assert records[3].message == "Application stopped"


@pytest.mark.parametrize("levels", [1, 2, 3])
def test_start_timeout(
    caplog: LogCaptureFixture, anyio_backend_name: str, levels: int
) -> None:
    class StallingComponent(Component):
        def __init__(self, level: int):
            super().__init__()
            self.level = level
            if self.level < levels:
                self.add_component("child1", StallingComponent, level=self.level + 1)
                self.add_component("child2", StallingComponent, level=self.level + 1)

        async def start(self) -> None:
            if self.level == levels:
                # Wait forever for a non-existent resource
                await get_resource(float, wait=True)

    caplog.set_level(logging.INFO)
    component = StallingComponent(1)
    with pytest.raises(SystemExit) as exc_info:
        run_application(component, start_timeout=0.1, backend=anyio_backend_name)

    assert exc_info.value.code == 1
    assert len(caplog.messages) == 4
    assert caplog.messages[0] == "Running in development mode"
    assert caplog.messages[1] == "Starting application"
    assert caplog.messages[2].startswith(
        "Timeout waiting for the root component to start\n"
        "Components still waiting to finish startup:\n"
    )
    assert caplog.messages[3] == "Application stopped"

    child_component_re = re.compile(r"([ |]+)\+-([a-z.12]+) \((.+)\)")
    lines = caplog.messages[2].splitlines()
    expected_test_name = f"{__name__}.test_start_timeout"
    assert lines[2] == f"  root ({expected_test_name}.<locals>.StallingComponent)"
    component_aliases: set[str] = set()
    depths: list[int] = [0] * levels
    expected_indent = "  |  "
    for line in lines[3:]:
        if match := child_component_re.match(line):
            indent, alias, component_name = match.groups()
            depth = len(alias.split("."))
            depths[depth - 1] += 1
            depths[depth:] = [0] * (len(depths) - depth)
            assert len(depths) == levels
            assert all(d < 3 for d in depths)
            expected_indent = "  " + "".join(
                ("  " if d > 1 else "| ") for d in depths[:depth]
            )
            assert component_name == (
                f"{expected_test_name}.<locals>.StallingComponent"
            )
            component_aliases.add(alias)
        else:
            assert line.startswith(expected_indent)

    if levels == 1:
        assert not component_aliases
    elif levels == 2:
        assert component_aliases == {"child1", "child2"}
    else:
        assert component_aliases == {
            "child1",
            "child2",
            "child1.child1",
            "child1.child2",
            "child2.child1",
            "child2.child2",
        }


def test_dict_config(caplog: LogCaptureFixture, anyio_backend_name: str) -> None:
    """Test that component configuration passed as a dictionary works."""
    caplog.set_level(logging.INFO)
    run_application(component={"type": DummyCLIApp}, backend=anyio_backend_name)

    records = [
        record for record in caplog.records if record.name == "asphalt.core._runner"
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message == "Application started"
    assert records[3].message == "Application stopped"


def test_run_cli_application(
    caplog: LogCaptureFixture, anyio_backend_name: str
) -> None:
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit) as exc:
        run_application(DummyCLIApp(20), backend=anyio_backend_name)

    assert exc.value.code == 20

    records = [
        record for record in caplog.records if record.name == "asphalt.core._runner"
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message == "Application started"
    assert records[3].message == "Application stopped"
