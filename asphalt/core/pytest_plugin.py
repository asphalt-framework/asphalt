"""Contains plugins for the py.test framework."""
import pytest


@pytest.fixture(autouse=True)
def setup_asphalt_event_loop(event_loop):
    from asphalt.core.concurrency import set_event_loop

    set_event_loop(event_loop)
