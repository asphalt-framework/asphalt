from __future__ import annotations

import logging
import platform
import signal
from typing import Any
from unittest.mock import patch

import anyio
import pytest
from _pytest.logging import LogCaptureFixture
from anyio import to_thread
from anyio.lowlevel import checkpoint
from common import raises_in_exception_group

from asphalt.core import (
    ApplicationExit,
    CLIApplicationComponent,
    Component,
    add_teardown_callback,
    request_resource,
    run_application,
    start_background_task,
)

pytestmark = pytest.mark.anyio()
windows_signal_mark = pytest.mark.skipif(
    platform.system() == "Windows", reason="Signals don't work on Windows"
)


class ShutdownComponent(Component):
    def __init__(self, method: str = "exit"):
        self.method = method
        self.teardown_callback_called = False
        self.exception: BaseException | None = None

    def teardown_callback(self, exception: BaseException | None) -> None:
        self.teardown_callback_called = True
        self.exception = exception

    async def stop_app(self) -> None:
        await checkpoint()
        if self.method == "exit":
            raise ApplicationExit
        elif self.method == "keyboard":
            signal.raise_signal(signal.SIGINT)
        elif self.method == "sigterm":
            signal.raise_signal(signal.SIGTERM)
        elif self.method == "exception":
            raise RuntimeError("this should crash the application")

    async def start(self) -> None:
        add_teardown_callback(self.teardown_callback, pass_exception=True)
        if self.method == "timeout":
            await anyio.sleep(1)
        else:
            await start_background_task(
                self.stop_app, "Application terminator", teardown_action=None
            )


class CrashComponent(Component):
    def __init__(self, method: str = "exit"):
        self.method = method

    async def start(self) -> None:
        if self.method == "keyboard":
            raise KeyboardInterrupt
        elif self.method == "sigterm":
            signal.raise_signal(signal.SIGTERM)
        elif self.method == "exception":
            raise RuntimeError("this should crash the application")


class DummyCLIApp(CLIApplicationComponent):
    async def run(self) -> int | None:
        return 20


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
async def test_run_logging_config(logging_config: dict[str, Any] | int | None) -> None:
    """Test that logging initialization happens as expected."""
    with patch("asphalt.core._runner.basicConfig") as basicConfig, patch(
        "asphalt.core._runner.dictConfig"
    ) as dictConfig:
        await run_application(ShutdownComponent(), logging=logging_config)

    assert basicConfig.call_count == (1 if logging_config == logging.INFO else 0)
    assert dictConfig.call_count == (1 if isinstance(logging_config, dict) else 0)


@pytest.mark.parametrize("max_threads", [None, 3])
async def test_run_max_threads(max_threads: int | None) -> None:
    """
    Test that a new default executor is installed if and only if the max_threads
    argument is given.

    """
    component = ShutdownComponent()
    limiter = to_thread.current_default_thread_limiter()
    expected_total_tokens = max_threads or limiter.total_tokens
    await run_application(component, max_threads=max_threads)
    assert limiter.total_tokens == expected_total_tokens


async def test_run_callbacks(caplog: LogCaptureFixture) -> None:
    """
    Test that the teardown callbacks are run when the application is started and shut
    down properly and that the proper logging messages are emitted.

    """
    caplog.set_level(logging.INFO)
    component = ShutdownComponent()
    await run_application(component)

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
        pytest.param("exit", None, id="exit"),
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
async def test_clean_exit(
    caplog: LogCaptureFixture, method: str, expected_stop_message: str | None
) -> None:
    """
    Test that when application termination is explicitly requested either externally or
    directly from a service task, it exits cleanly.

    """
    caplog.set_level(logging.INFO)
    component = ShutdownComponent(method=method)
    await run_application(component)

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


async def test_start_exception(caplog: LogCaptureFixture) -> None:
    """
    Test that an exception caught during the application initialization is put into the
    application context and made available to teardown callbacks.

    """
    caplog.set_level(logging.INFO)
    component = CrashComponent(method="exception")
    with raises_in_exception_group(
        RuntimeError, match="this should crash the application"
    ):
        await run_application(component)

    records = [
        record for record in caplog.records if record.name.startswith("asphalt.core.")
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message == "Error during application startup"
    assert records[3].message == "Application stopped"


async def test_start_timeout(caplog: LogCaptureFixture) -> None:
    """
    Test that when the root component takes too long to start up, the runner exits and
    logs the appropriate error message.

    """

    class StallingComponent(Component):
        async def start(self) -> None:
            # Wait forever for a non-existent resource
            await request_resource(float)

    caplog.set_level(logging.INFO)
    component = StallingComponent()
    with raises_in_exception_group(TimeoutError):
        await run_application(component, start_timeout=0.1)

    records = [
        record for record in caplog.records if record.name == "asphalt.core._runner"
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message.startswith(
        "Timeout waiting for the root component to start"
    )
    # assert "-> await ctx.get_resource(float)" in records[2].message
    assert records[3].message == "Application stopped"


async def test_dict_config(caplog: LogCaptureFixture) -> None:
    """Test that component configuration passed as a dictionary works."""
    caplog.set_level(logging.INFO)
    await run_application(component={"type": ShutdownComponent})

    records = [
        record for record in caplog.records if record.name == "asphalt.core._runner"
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message == "Application started"
    assert records[3].message == "Application stopped"


async def test_run_cli_application(caplog: LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit) as exc:
        await run_application(DummyCLIApp())

    assert exc.value.code == 20

    records = [
        record for record in caplog.records if record.name == "asphalt.core._runner"
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message == "Application started"
    assert records[3].message == "Application stopped"
