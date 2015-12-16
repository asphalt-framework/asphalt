from asyncio import coroutine
import threading
import asyncio

import pytest

from asphalt.core.concurrency import blocking, asynchronous, set_event_loop


@pytest.fixture(autouse=True)
def set_current_event_loop(event_loop):
    set_event_loop(event_loop)


@pytest.mark.asyncio
@pytest.mark.parametrize('run_in_threadpool', [False, True], ids=['eventloop', 'threadpool'])
def test_wrap_blocking_callable(event_loop, run_in_threadpool):
    @blocking
    def func(x, y):
        assert threading.current_thread() is not threading.main_thread()
        return x + y

    if run_in_threadpool:
        retval = yield from event_loop.run_in_executor(None, func, 1, 2)
    else:
        retval = yield from func(1, 2)

    assert retval == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('run_in_threadpool', [False, True], ids=['eventloop', 'threadpool'])
def test_wrap_async_callable(event_loop, run_in_threadpool):
    @asynchronous
    @coroutine
    def func(x, y):
        assert threading.current_thread() is threading.main_thread()
        yield from asyncio.sleep(0.2)
        return x + y

    if run_in_threadpool:
        retval = yield from event_loop.run_in_executor(None, func, 1, 2)
    else:
        retval = yield from func(1, 2)

    assert retval == 3


@pytest.mark.asyncio
def test_wrap_async_callable_exception(event_loop):
    @coroutine
    def async_func():
        yield from asyncio.sleep(0.2)
        raise ValueError('test')

    wrapped_async_func = asynchronous(async_func)
    with pytest.raises(ValueError) as exc:
        yield from event_loop.run_in_executor(None, wrapped_async_func)
    assert str(exc.value) == 'test'
