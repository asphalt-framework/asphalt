# isort: off
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from anyio import wait_all_tasks_blocked
from pytest import CaptureFixture
from asphalt.core import Context, start_component

from echo.client import ClientComponent
from echo.server import ServerComponent

pytestmark = pytest.mark.anyio


@pytest.fixture
async def server(capsys: CaptureFixture[str]) -> AsyncGenerator[None, None]:
    async with Context():
        server = ServerComponent()
        await start_component(server)
        yield


async def test_client_and_server(server: None, capsys: CaptureFixture[str]) -> None:
    async with Context():
        client = ClientComponent("Hello!")
        await start_component(client)
        await client.run()

    # Grab the captured output of sys.stdout and sys.stderr from the capsys fixture
    await wait_all_tasks_blocked()
    out, err = capsys.readouterr()
    assert "Message from client: Hello!" in out
    assert "Server responded: Hello!" in out
