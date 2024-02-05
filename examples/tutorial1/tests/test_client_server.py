# isort: off
import pytest

from asphalt.core import Context
from pytest import CaptureFixture

from echo.client import ClientComponent
from echo.server import ServerComponent

pytestmark = pytest.mark.anyio


async def test_client_and_server(capsys: CaptureFixture[str]) -> None:
    async with Context():
        server = ServerComponent()
        await server.start()

        client = ClientComponent("Hello!")
        await client.start()

    # Grab the captured output of sys.stdout and sys.stderr from the capsys fixture
    out, err = capsys.readouterr()
    assert out == "Message from client: Hello!\nServer responded: Hello!\n"
