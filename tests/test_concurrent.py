from __future__ import annotations

import sys
from typing import NoReturn

import anyio
import pytest
from anyio import Event, fail_after, get_current_task, sleep
from anyio.abc import TaskStatus
from pytest import LogCaptureFixture

from asphalt.core import (
    Component,
    ComponentContext,
    ComponentStartError,
    Context,
    TaskFactory,
    TaskHandle,
    start_component,
)

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup

pytestmark = pytest.mark.anyio()


class TestTaskFactory:
    async def test_start(self) -> None:
        handle: TaskHandle | None = None

        async def taskfunc() -> str:
            assert get_current_task().name == "taskfunc"
            return "returnvalue"

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                nonlocal handle
                factory = await ctx.start_background_task_factory()
                handle = await factory.start_task(taskfunc, "taskfunc")

        async with Context():
            await start_component(TaskComponent)
            assert handle is not None
            assert handle.start_value is None
            await handle.wait_finished()

    async def test_start_empty_name(self) -> None:
        handle: TaskHandle | None = None

        async def taskfunc() -> None:
            assert get_current_task().name == expected_name

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                nonlocal handle
                factory = await ctx.start_background_task_factory()
                handle = await factory.start_task(taskfunc)

        expected_name = (
            f"{__name__}.{self.__class__.__name__}.test_start_empty_name.<locals>"
            f".taskfunc"
        )
        async with Context():
            await start_component(TaskComponent)
            assert handle is not None
            assert handle.name == expected_name

    async def test_start_status(self) -> None:
        handle: TaskHandle | None = None

        async def taskfunc(task_status: TaskStatus[str]) -> str:
            assert get_current_task().name == "taskfunc"
            task_status.started("startval")
            return "returnvalue"

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                nonlocal handle
                factory = await ctx.start_background_task_factory()
                handle = await factory.start_task(taskfunc, "taskfunc")

        async with Context():
            await start_component(TaskComponent)
            assert handle is not None
            assert handle.start_value == "startval"
            await handle.wait_finished()

    async def test_start_cancel(self) -> None:
        started = False
        finished = False
        handle: TaskHandle | None = None

        async def taskfunc() -> None:
            nonlocal started, finished
            assert get_current_task().name == "taskfunc"
            started = True
            await sleep(3)
            finished = True

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                nonlocal handle
                factory = await ctx.start_background_task_factory()
                handle = await factory.start_task(taskfunc, "taskfunc")

        async with Context():
            await start_component(TaskComponent)
            assert handle is not None
            handle.cancel()

        assert started
        assert not finished

    async def test_start_exception(self) -> None:
        async def taskfunc() -> NoReturn:
            await sleep(0)
            raise Exception("foo")

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                factory = await ctx.start_background_task_factory()
                await factory.start_task(taskfunc, "taskfunc")

        with pytest.raises(ExceptionGroup) as excinfo:
            async with Context():
                await start_component(TaskComponent)

        assert len(excinfo.value.exceptions) == 1
        assert isinstance(excinfo.value.exceptions[0], ExceptionGroup)
        excgrp = excinfo.value.exceptions[0]
        assert len(excgrp.exceptions) == 1
        assert str(excgrp.exceptions[0]) == "foo"

    async def test_start_exception_handled(self) -> None:
        handled_exception: Exception | None = None

        def handle_exception(exc: Exception) -> bool:
            nonlocal handled_exception
            handled_exception = exc
            return True

        async def taskfunc() -> NoReturn:
            raise Exception("foo")

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                factory = await ctx.start_background_task_factory(
                    exception_handler=handle_exception
                )
                await factory.start_task(taskfunc, "taskfunc")

        async with Context():
            await start_component(TaskComponent)

        assert str(handled_exception) == "foo"

    @pytest.mark.parametrize("name", ["taskname", None])
    async def test_start_soon(self, name: str | None) -> None:
        expected_name = (
            name
            or f"{__name__}.{self.__class__.__name__}.test_start_soon.<locals>.taskfunc"
        )
        handle: TaskHandle | None = None

        async def taskfunc() -> str:
            assert get_current_task().name == expected_name
            return "returnvalue"

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                nonlocal handle
                factory = await ctx.start_background_task_factory()
                handle = factory.start_task_soon(taskfunc, name)

        async with Context():
            await start_component(TaskComponent)
            assert handle is not None
            await handle.wait_finished()

        assert handle.name == expected_name

    async def test_all_task_handles(self) -> None:
        factory: TaskFactory | None = None
        handle1: TaskHandle | None = None
        handle2: TaskHandle | None = None
        event = Event()

        async def taskfunc() -> None:
            await event.wait()

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                nonlocal factory, handle1, handle2
                factory = await ctx.start_background_task_factory()
                handle1 = await factory.start_task(taskfunc)
                handle2 = factory.start_task_soon(taskfunc)

        async with Context():
            await start_component(TaskComponent)
            assert factory is not None
            assert handle1 is not None
            assert handle2 is not None
            assert factory.all_task_handles() == {handle1, handle2}
            event.set()
            for handle in (handle1, handle2):
                await handle.wait_finished()

            assert factory.all_task_handles() == set()


class TestServiceTask:
    async def test_bad_teardown_action(self, caplog: LogCaptureFixture) -> None:
        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                await ctx.start_service_task(
                    lambda: sleep(1),
                    "Dummy",
                    teardown_action="fail",  # type: ignore[arg-type]
                )

        async with Context():
            with pytest.raises(
                ComponentStartError, match="teardown_action must be a callable"
            ):
                await start_component(TaskComponent)

    async def test_teardown_async(self) -> None:
        async def teardown_callback() -> None:
            event.set()

        async def service_func() -> None:
            await event.wait()

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                await ctx.start_service_task(
                    service_func, "Dummy", teardown_action=teardown_callback
                )

        event = anyio.Event()
        with fail_after(1):
            async with Context():
                await start_component(TaskComponent)

    async def test_teardown_fail(self, caplog: LogCaptureFixture) -> None:
        def teardown_callback() -> NoReturn:
            raise Exception("foo")

        async def service_func() -> None:
            await event.wait()

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                await ctx.start_service_task(
                    service_func, "Dummy", teardown_action=teardown_callback
                )

        event = anyio.Event()
        with fail_after(1):
            async with Context():
                await start_component(TaskComponent)

        assert caplog.messages == [
            f"Error calling teardown callback ({__name__}.{self.__class__.__name__}"
            f".test_teardown_fail.<locals>.teardown_callback) for service task 'Dummy'"
        ]

    async def test_start_service_task_cancel_on_exit(self) -> None:
        started = False
        finished = False

        async def taskfunc() -> None:
            nonlocal started, finished
            assert get_current_task().name == "Service task: taskfunc"
            started = True
            await sleep(3)
            finished = True

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                await ctx.start_service_task(
                    taskfunc, "taskfunc", teardown_action="cancel"
                )

        async with Context():
            await start_component(TaskComponent)

        assert started
        assert not finished

    async def test_start_service_task_status(self) -> None:
        started = False
        finished = False

        async def taskfunc(task_status: TaskStatus[str]) -> None:
            nonlocal started, finished
            assert get_current_task().name == "Service task: taskfunc"
            started = True
            task_status.started("startval")
            await sleep(3)
            finished = True

        class TaskComponent(Component):
            async def start(self, ctx: ComponentContext) -> None:
                startval = await ctx.start_service_task(
                    taskfunc, "taskfunc", teardown_action="cancel"
                )
                assert startval == "startval"

        async with Context():
            await start_component(TaskComponent)

        assert started
        assert not finished
