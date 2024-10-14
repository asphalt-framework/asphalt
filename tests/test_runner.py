from __future__ import annotations

import logging
import platform
import re
import signal
from textwrap import dedent
from typing import Any, Literal
from unittest.mock import patch

import anyio
import pytest
from _pytest.logging import LogCaptureFixture
from anyio import sleep, to_thread, wait_all_tasks_blocked

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
        await wait_all_tasks_blocked()
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
        self.exception: BaseException | None = None

    def teardown_callback(self, exception: BaseException | None) -> None:
        logging.getLogger(__name__).info("Teardown callback called")
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
    with (
        patch("asphalt.core._runner.basicConfig") as basicConfig,
        patch("asphalt.core._runner.dictConfig") as dictConfig,
    ):
        run_application(DummyCLIApp, logging=logging_config, backend=anyio_backend_name)

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

    expected_total_tokens = max_threads or anyio.run(
        get_default_total_tokens, backend=anyio_backend_name
    )
    run_application(
        MaxThreadsComponent, max_threads=max_threads, backend=anyio_backend_name
    )
    assert observed_total_tokens == expected_total_tokens


def test_run_callbacks(caplog: LogCaptureFixture, anyio_backend_name: str) -> None:
    """
    Test that the teardown callbacks are run when the application is started and shut
    down properly and that the proper logging messages are emitted.

    """
    caplog.set_level(logging.INFO)
    run_application(DummyCLIApp, backend=anyio_backend_name)

    assert caplog.messages == [
        "Running in development mode",
        "Starting application",
        "Application started",
        "Teardown callback called",
        "Application stopped",
    ]


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
    caplog.set_level(logging.INFO, "asphalt.core")
    run_application(ShutdownComponent, {"method": method}, backend=anyio_backend_name)

    expected_messages = [
        "Running in development mode",
        "Starting application",
        "Application started",
        "Application stopped",
    ]
    if expected_stop_message:
        expected_messages.insert(3, expected_stop_message)

    assert caplog.messages == expected_messages


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
    caplog.set_level(logging.INFO, "asphalt.core")
    with pytest.raises(SystemExit) as exc_info:
        run_application(CrashComponent, {"method": method}, backend=anyio_backend_name)

    assert exc_info.value.code == 1
    assert caplog.messages == [
        "Running in development mode",
        "Starting application",
        expected_stop_message,
        "Application stopped",
    ]


def test_start_timeout(caplog: LogCaptureFixture, anyio_backend_name: str) -> None:
    class StallingComponent(Component):
        def __init__(self, level: int = 1):
            super().__init__()
            self.is_leaf = level == 4
            if not self.is_leaf:
                self.add_component("child1", StallingComponent, level=level + 1)
                self.add_component("child2", StallingComponent, level=level + 1)

        async def start(self) -> None:
            if self.is_leaf:
                # Wait forever for a non-existent resource
                await get_resource(float)

    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit) as exc_info:
        run_application(
            StallingComponent,
            {},
            start_timeout=0.1,
            backend=anyio_backend_name,
        )

    assert exc_info.value.code == 1
    assert caplog.messages == [
        "Running in development mode",
        "Starting application",
        caplog.messages[2],
        "Application stopped",
    ]
    assert caplog.messages[2].startswith(
        dedent(
            """\
            Timeout waiting for the component tree to start

            Current status of the components still waiting to finish startup
            ----------------------------------------------------------------

            (root): starting children
              child1: starting children
                child1: starting children
                  child1: starting
                  child2: starting
                child2: starting children
                  child1: starting
                  child2: starting
              child2: starting children
                child1: starting children
                  child1: starting
                  child2: starting
                child2: starting children
                  child1: starting
                  child2: starting

            Stack summaries of components still waiting to start
            ----------------------------------------------------

            """
        )
    )

    expected_component_name = (
        f"{__name__}.test_start_timeout.<locals>.StallingComponent"
    )
    paths = set()
    for line in caplog.messages[2].splitlines()[24:]:
        if line.startswith("    "):
            pass
        elif line.startswith("  "):
            assert line.startswith('  File "')
        elif line:
            match = re.match(r"(child\d\.child\d\.child\d) \((.+)\):$", line)
            assert match
            paths.add(match.group(1))
            assert match.group(2) == expected_component_name

    assert paths == {
        "child1.child1.child1",
        "child1.child1.child2",
        "child1.child2.child1",
        "child1.child2.child2",
        "child2.child1.child1",
        "child2.child1.child2",
        "child2.child2.child1",
        "child2.child2.child2",
    }


def test_run_cli_application(
    caplog: LogCaptureFixture, anyio_backend_name: str
) -> None:
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit) as exc:
        run_application(DummyCLIApp, {"exit_code": 20}, backend=anyio_backend_name)

    assert exc.value.code == 20

    assert caplog.messages == [
        "Running in development mode",
        "Starting application",
        "Application started",
        "Teardown callback called",
        "Application stopped",
    ]
