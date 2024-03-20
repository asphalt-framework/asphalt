from __future__ import annotations

import sys
from typing import NoReturn

import pytest
from anyio import get_current_task, sleep
from anyio.abc import TaskStatus

from asphalt.core import (
    Context,
    TaskFactory,
    require_resource,
    start_background_task_factory,
)

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup

pytestmark = pytest.mark.anyio()


async def test_start_background_task() -> None:
    async def taskfunc() -> str:
        assert get_current_task().name == "taskfunc"
        return "returnvalue"

    async with Context():
        factory = await start_background_task_factory()
        assert factory is require_resource(TaskFactory)
        handle = await factory.start_task(taskfunc, "taskfunc")
        assert handle.start_value is None
        await handle.wait_finished()


async def test_start_background_task_status() -> None:
    async def taskfunc(task_status: TaskStatus[str]) -> str:
        assert get_current_task().name == "taskfunc"
        task_status.started("startval")
        return "returnvalue"

    async with Context():
        factory = await start_background_task_factory()
        handle = await factory.start_task(taskfunc, "taskfunc")
        assert handle.start_value == "startval"
        await handle.wait_finished()


async def test_start_background_task_cancel() -> None:
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


async def test_start_background_task_exception() -> None:
    async def taskfunc() -> NoReturn:
        raise Exception("foo")

    with pytest.raises(ExceptionGroup) as excinfo:
        async with Context():
            factory = await start_background_task_factory()
            await factory.start_task(taskfunc, "taskfunc")

    assert len(excinfo.value.exceptions) == 1
    assert isinstance(excinfo.value.exceptions[0], ExceptionGroup)
    excgrp = excinfo.value.exceptions[0]
    assert len(excgrp.exceptions) == 1
    assert str(excgrp.exceptions[0]) == "foo"


async def test_start_background_task_exception_handled() -> None:
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
