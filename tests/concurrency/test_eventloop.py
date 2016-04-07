from asyncio.events import new_event_loop

import pytest

from asphalt.core.concurrency.eventloop import get_event_loop, set_event_loop, is_event_loop_thread


def test_get_set_current_event_loop():
    new_loop = new_event_loop()
    set_event_loop(new_loop)
    assert get_event_loop() is new_loop


@pytest.mark.asyncio
async def test_is_event_loop_thread(event_loop):
    assert is_event_loop_thread()
    assert not await event_loop.run_in_executor(None, is_event_loop_thread)
