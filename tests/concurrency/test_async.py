import asyncio

import pytest

from asphalt.core.concurrency.async import yield_async, async_contextmanager, async_generator
from asphalt.core.concurrency.threads import call_in_thread


class TestAsyncContextManager:
    @pytest.mark.asyncio
    async def test_async_contextmanager(self):
        @async_contextmanager
        async def dummycontext(value):
            await yield_async(value)

        async with dummycontext(2) as value:
            assert value == 2

    @pytest.mark.asyncio
    async def test_async_contextmanager_three_awaits(self):
        @async_contextmanager
        async def dummycontext(value):
            await asyncio.sleep(0.1)
            await yield_async(value)
            await asyncio.sleep(0.1)

        async with dummycontext(2) as value:
            assert value == 2

    @pytest.mark.asyncio
    async def test_async_contextmanager_no_yield(self):
        @async_contextmanager
        async def dummycontext():
            pass

        with pytest.raises(RuntimeError) as exc:
            async with dummycontext():
                pass

        assert str(exc.value) == 'coroutine finished without yielding a value'

    @pytest.mark.asyncio
    async def test_async_contextmanager_extra_yield(self):
        @async_contextmanager
        async def dummycontext(value):
            await yield_async(value)
            await yield_async(3)

        with pytest.raises(RuntimeError) as exc:
            async with dummycontext(2) as value:
                assert value == 2

        assert str(exc.value) == 'coroutine yielded a value in the exit phase: 3'


class TestAsyncGenerator:
    @pytest.mark.asyncio
    async def test_yield(self):
        """Test that values yielded by yield_async() end up in the consumer."""
        @async_generator
        async def dummygenerator(start):
            await yield_async(start)
            await yield_async(start + 1)
            await yield_async(start + 2)

        values = []
        async for value in dummygenerator(2):
            values.append(value)

        assert values == [2, 3, 4]

    @pytest.mark.asyncio
    async def test_exception(self):
        """Test that an exception raised directly in the async generator is properly propagated."""
        @async_generator
        async def dummygenerator(start):
            await yield_async(start)
            raise ValueError('foo')

        values = []
        with pytest.raises(ValueError) as exc:
            async for value in dummygenerator(2):
                values.append(value)

        assert values == [2]
        assert str(exc.value) == 'foo'

    @pytest.mark.asyncio
    async def test_awaitable_exception(self):
        """
        Test that an exception raised in something awaited by the async generator is sent back to
        the generator.

        """
        def raise_error():
            raise ValueError('foo')

        @async_generator
        async def dummygenerator():
            try:
                await call_in_thread(raise_error)
            except ValueError as e:
                await yield_async(e)

        values = []
        async for value in dummygenerator():
            values.append(value)

        assert str(values[0]) == 'foo'
