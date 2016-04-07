import threading
from concurrent.futures.thread import ThreadPoolExecutor

import pytest

from asphalt.core.concurrency.eventloop import is_event_loop_thread
from asphalt.core.concurrency.threads import threadpool, call_in_thread, call_async


class TestThreadpool:
    @pytest.mark.parametrize('already_in_thread', [False, True])
    @pytest.mark.asyncio
    async def test_threadpool_decorator_noargs(self, event_loop, already_in_thread):
        """Test that threadpool() without arguments works as a decorator."""
        @threadpool
        def func(x, y):
            nonlocal func_thread
            func_thread = threading.current_thread()
            return x + y

        event_loop_thread = threading.current_thread()
        func_thread = None
        callback = (event_loop.run_in_executor(None, func, 1, 2) if already_in_thread else
                    func(1, 2))
        assert await callback == 3
        assert func_thread is not event_loop_thread

    @pytest.mark.parametrize('executor', [None, ThreadPoolExecutor(1)])
    @pytest.mark.asyncio
    async def test_threadpool_decorator(self, executor):
        """Test that threadpool() with an argument works as a decorator."""
        @threadpool(executor)
        def func(x, y):
            nonlocal func_thread
            func_thread = threading.current_thread()
            return x + y

        event_loop_thread = threading.current_thread()
        func_thread = None
        assert await func(1, 2) == 3
        assert func_thread is not event_loop_thread

    @pytest.mark.asyncio
    async def test_threadpool_contextmanager(self):
        """Test that threadpool() with an argument works as a context manager."""
        event_loop_thread = threading.current_thread()

        async with threadpool():
            func_thread = threading.current_thread()

        assert threading.current_thread() is event_loop_thread
        assert func_thread is not event_loop_thread

    @pytest.mark.asyncio
    async def test_threadpool_contextmanager_exception(self):
        """Test that an exception raised from a threadpool block is properly propagated."""
        event_loop_thread = threading.current_thread()

        with pytest.raises(ValueError) as exc:
            async with threadpool():
                raise ValueError('foo')

        assert threading.current_thread() is event_loop_thread
        assert str(exc.value) == 'foo'


class TestCallInThread:
    @pytest.mark.parametrize('executor', [None, ThreadPoolExecutor(1)])
    @pytest.mark.asyncio
    async def test_call_in_thread(self, executor):
        """Test that call_in_thread actually runs the target in a worker thread."""
        assert not await call_in_thread(is_event_loop_thread, executor=executor)

    @pytest.mark.asyncio
    async def test_async_eventloop_thread(self, event_loop):
        """Test that call_in_thread can only be called from the event loop thread."""
        with pytest.raises(AssertionError) as exc:
            await event_loop.run_in_executor(None, call_in_thread, is_event_loop_thread)

        assert str(exc.value) == 'call_in_thread() must be called in the event loop thread'


class TestCallAsync:
    @pytest.mark.asyncio
    async def test_call_async_plain(self, event_loop):
        def runs_in_event_loop():
            assert is_event_loop_thread()

        await event_loop.run_in_executor(None, call_async, runs_in_event_loop)

    @pytest.mark.asyncio
    async def test_call_async_coroutine(self, event_loop):
        async def runs_in_event_loop():
            assert is_event_loop_thread()

        await event_loop.run_in_executor(None, call_async, runs_in_event_loop)

    @pytest.mark.asyncio
    async def test_call_async_exception(self, event_loop):
        def runs_in_event_loop():
            raise ValueError('foo')

        with pytest.raises(ValueError) as exc:
            await event_loop.run_in_executor(None, call_async, runs_in_event_loop)

        assert str(exc.value) == 'foo'

    @pytest.mark.asyncio
    async def test_call_async_eventloop_thread(self):
        def runs_in_event_loop():
            assert is_event_loop_thread()

        with pytest.raises(AssertionError) as exc:
            await call_async(runs_in_event_loop)

        assert str(exc.value) == 'call_async() must be called in a worker thread'
