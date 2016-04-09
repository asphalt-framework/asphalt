from asyncio.events import new_event_loop

import pytest

from asphalt.core.concurrency import (get_event_loop, set_event_loop, is_event_loop_thread,
                                      call_async)


def test_get_set_current_event_loop():
    new_loop = new_event_loop()
    set_event_loop(new_loop)
    assert get_event_loop() is new_loop


@pytest.mark.asyncio
async def test_is_event_loop_thread(event_loop):
    assert is_event_loop_thread()
    assert not await event_loop.run_in_executor(None, is_event_loop_thread)


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
