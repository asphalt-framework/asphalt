import asyncio
import sys

import pytest


def pytest_ignore_collect(path, config):
    return path.basename.endswith('_py36.py') and sys.version_info < (3, 6)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
