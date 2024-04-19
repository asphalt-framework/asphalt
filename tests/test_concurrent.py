from __future__ import annotations

import sys
from typing import NoReturn

import anyio
import pytest
from anyio import fail_after, get_current_task, sleep
from anyio.abc import TaskStatus
from pytest import LogCaptureFixture

from asphalt.core import (
    Context,
    TaskFactory,
    get_resource_nowait,
    start_background_task_factory,
    start_service_task,
)

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup

pytestmark = pytest.mark.anyio()


class TestTaskFactory:
    async def test_resource(self) -> None:
        async with Context():
            default_factory = await start_background_task_factory()
            other_factory = await start_background_task_factory("other")
            assert default_factory is get_resource_nowait(TaskFactory)
            assert other_factory is get_resource_nowait(TaskFactory, "other")

    async def test_start(self) -> None:
        async def taskfunc() -> str:
            assert get_current_task().name == "taskfunc"
            return "returnvalue"

        async with Context():
            factory = await start_background_task_factory()
            handle = await factory.start_task(taskfunc, "taskfunc")
            assert handle.start_value is None
            await handle.wait_finished()

    async def test_start_empty_name(self) -> None:
        async def taskfunc() -> None:
            assert get_current_task().name == expected_name

        expected_name = (
            f"{__name__}.{self.__class__.__name__}.test_start_empty_name.<locals>"
            f".taskfunc"
        )
        async with Context():
            factory = await start_background_task_factory()
            handle = await factory.start_task(taskfunc)
            assert handle.name == expected_name

    async def test_start_in_subcontext(self) -> None:
        async def taskfunc() -> str:
            assert get_current_task().name == "taskfunc"
            return "returnvalue"

        async with Context(), Context():
            factory = await start_background_task_factory()
            handle = await factory.start_task(taskfunc, "taskfunc")
            assert handle.start_value is None
            await handle.wait_finished()

    async def test_start_status(self) -> None:
        async def taskfunc(task_status: TaskStatus[str]) -> str:
            assert get_current_task().name == "taskfunc"
            task_status.started("startval")
            return "returnvalue"

        async with Context():
            factory = await start_background_task_factory()
            handle = await factory.start_task(taskfunc, "taskfunc")
            assert handle.start_value == "startval"
            await handle.wait_finished()

    async def test_start_cancel(self) -> None:
        started = False
        finished = False

        async def taskfunc() -> None:
            nonlocal started, finished
            assert get_current_task().name == "taskfunc"
            started = True
            await sleep(3)
            finished = True

        async with Context():
            factory = await start_background_task_factory()
            handle = await factory.start_task(taskfunc, "taskfunc")
            handle.cancel()

        assert started
        assert not finished

    async def test_start_exception(self) -> None:
        async def taskfunc() -> NoReturn:
            raise Exception("foo")

        with pytest.raises(ExceptionGroup) as excinfo:
            async with Context():
                factory = await start_background_task_factory()
                await factory.start_task(taskfunc, "taskfunc")

        assert len(excinfo.value.exceptions) == 1
        assert isinstance(excinfo.value.exceptions[0], ExceptionGroup)
        excgrp0 = excinfo.value.exceptions[0]
        assert len(excgrp0.exceptions) == 1
        assert isinstance(excgrp0, ExceptionGroup)
        excgrp1 = excgrp0.exceptions[0]
        assert isinstance(excgrp1, ExceptionGroup)
        assert len(excgrp1.exceptions) == 1
        assert str(excgrp1.exceptions[0]) == "foo"

    async def test_start_exception_handled(self) -> None:
        handled_exception: Exception | None = None

        def handle_exception(exc: Exception) -> bool:
            nonlocal handled_exception
            handled_exception = exc
            return True

        async def taskfunc() -> NoReturn:
            raise Exception("foo")

        async with Context():
            factory = await start_background_task_factory(
                exception_handler=handle_exception
            )
            await factory.start_task(taskfunc, "taskfunc")

        assert str(handled_exception) == "foo"

    @pytest.mark.parametrize("name", ["taskname", None])
    async def test_start_soon(self, name: str | None) -> None:
        expected_name = (
            name
            or f"{__name__}.{self.__class__.__name__}.test_start_soon.<locals>.taskfunc"
        )

        async def taskfunc() -> str:
            assert get_current_task().name == expected_name
            return "returnvalue"

        async with Context():
            factory = await start_background_task_factory()
            handle = factory.start_task_soon(taskfunc, name)
            await handle.wait_finished()

        assert handle.name == expected_name

    async def test_cancel_all_tasks(self) -> None:
        async def taskfunc(task_status: TaskStatus[None]) -> None:
            task_status.started()
            await sleep(1)
            raise RuntimeError("this exception should not be raised")

        async with Context():
            factory = await start_background_task_factory()
            await factory.start_task(taskfunc)
            await factory.start_task(taskfunc)
            factory.cancel_all_tasks()

    async def test_wait_all_tasks_finished(self) -> None:
        return_values = []

        async def taskfunc(task_status: TaskStatus[None]) -> None:
            task_status.started()
            return_values.append("returnvalue")

        async with Context():
            factory = await start_background_task_factory()
            await factory.start_task(taskfunc)
            await factory.start_task(taskfunc)
            await factory.wait_all_tasks_finished()
            assert return_values == 2 * ["returnvalue"]
            return_values.clear()
            await factory.start_task(taskfunc)
            await factory.wait_all_tasks_finished()
            assert return_values == ["returnvalue"]


class TestServiceTask:
    async def test_bad_teardown_action(self, caplog: LogCaptureFixture) -> None:
        async def service_func() -> None:
            await event.wait()

        event = anyio.Event()
        async with Context():
            with pytest.raises(ValueError, match="teardown_action must be a callable"):
                await start_service_task(
                    service_func,
                    "Dummy",
                    teardown_action="fail",  # type: ignore[arg-type]
                )

    async def test_teardown_async(self) -> None:
        async def teardown_callback() -> None:
            event.set()

        async def service_func() -> None:
            await event.wait()

        event = anyio.Event()
        with fail_after(1):
            async with Context():
                await start_service_task(
                    service_func, "Dummy", teardown_action=teardown_callback
                )

    async def test_teardown_fail(self, caplog: LogCaptureFixture) -> None:
        def teardown_callback() -> NoReturn:
            raise Exception("foo")

        async def service_func() -> None:
            await event.wait()

        event = anyio.Event()
        with fail_after(1):
            async with Context():
                await start_service_task(
                    service_func, "Dummy", teardown_action=teardown_callback
                )

        assert caplog.messages == [
            f"Error calling teardown callback ({__name__}.{self.__class__.__name__}"
            f".test_teardown_fail.<locals>.teardown_callback) for service task 'Dummy'"
        ]
