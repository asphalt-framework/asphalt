# isort: off
import pytest

from asphalt.core import Context
from _pytest.capture import CaptureFixture

from echo.client import ClientComponent
from echo.server import ServerComponent

pytestmark = pytest.mark.anyio


async def test_client_and_server(capsys: CaptureFixture[str]) -> None:
    async with Context() as ctx:
        server = ServerComponent()
        await server.start(ctx)

        client = ClientComponent("Hello!")
        await client.start(ctx)

    # Grab the captured output of sys.stdout and sys.stderr from the capsys fixture
    out, err = capsys.readouterr()
    assert out == "Message from client: Hello!\nServer responded: Hello!\n"
