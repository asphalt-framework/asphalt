from __future__ import annotations

import logging
import platform
import signal
from unittest.mock import patch

import anyio
import pytest
from anyio import to_thread
from anyio.lowlevel import checkpoint

from asphalt.core import (
    ApplicationExit,
    CLIApplicationComponent,
    Component,
    Context,
    run_application,
    start_service_task,
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

    def teardown_callback(self, exception: BaseException) -> None:
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

    async def start(self, ctx: Context) -> None:
        ctx.add_teardown_callback(self.teardown_callback, pass_exception=True)
        if self.method == "timeout":
            await anyio.sleep(1)
        else:
            start_service_task(self.stop_app, name="Application terminator")


class CrashComponent(Component):
    def __init__(self, method: str = "exit"):
        self.method = method

    async def start(self, ctx: Context) -> None:
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
    [None, logging.INFO, {"version": 1, "loggers": {"asphalt": {"level": "INFO"}}}],
    ids=["disabled", "loglevel", "dictconfig"],
)
async def test_run_logging_config(logging_config):
    """Test that logging initialization happens as expected."""
    with patch("asphalt.core._runner.basicConfig") as basicConfig, patch(
        "asphalt.core._runner.dictConfig"
    ) as dictConfig:
        await run_application(ShutdownComponent(), logging=logging_config)

    assert basicConfig.call_count == (1 if logging_config == logging.INFO else 0)
    assert dictConfig.call_count == (1 if isinstance(logging_config, dict) else 0)


@pytest.mark.parametrize("max_threads", [None, 3])
async def test_run_max_threads(max_threads):
    """
    Test that a new default executor is installed if and only if the max_threads
    argument is given.

    """
    component = ShutdownComponent()
    limiter = to_thread.current_default_thread_limiter()
    expected_total_tokens = max_threads or limiter.total_tokens
    await run_application(component, max_threads=max_threads)
    assert limiter.total_tokens == expected_total_tokens


async def test_run_callbacks(caplog):
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
async def test_clean_exit(caplog, method: str, expected_stop_message: str | None):
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


async def test_start_exception(caplog):
    """
    Test that an exception caught during the application initialization is put into the
    application context and made available to teardown callbacks.

    """
    caplog.set_level(logging.INFO)
    component = CrashComponent(method="exception")
    with pytest.raises(RuntimeError, match="this should crash the application"):
        await run_application(component)

    records = [
        record for record in caplog.records if record.name.startswith("asphalt.core.")
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message == "Error during application startup"
    assert records[3].message == "Application stopped"


async def test_start_timeout(caplog):
    """
    Test that when the root component takes too long to start up, the runner exits and
    logs the appropriate error message.

    """

    class StallingComponent(Component):
        async def start(self, ctx: Context) -> None:
            # Wait forever for a non-existent resource
            await ctx.request_resource(float)

    caplog.set_level(logging.INFO)
    component = StallingComponent()
    with pytest.raises(TimeoutError):
        await run_application(component, start_timeout=0.1)

    records = [
        record for record in caplog.records if record.name == "asphalt.core._runner"
    ]
    assert len(records) == 4
    assert records[0].message == "Running in development mode"
    assert records[1].message == "Starting application"
    assert records[2].message.startswith(
        "Timeout waiting for the root component to start – exiting.\n"
    )
    assert "-> await ctx.request_resource(float)" in records[2].message
    assert records[3].message == "Application stopped"


async def test_dict_config(caplog):
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


async def test_run_cli_application(caplog):
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
