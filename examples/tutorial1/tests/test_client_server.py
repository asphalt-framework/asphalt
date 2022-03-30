# isort: off
import pytest

from asphalt.core import Context

from echo.client import ClientComponent
from echo.server import ServerComponent


def test_client_and_server(event_loop, capsys):
    async def run():
        async with Context() as ctx:
            server = ServerComponent()
            await server.start(ctx)

            client = ClientComponent("Hello!")
            await client.start(ctx)

    event_loop.create_task(run())
    with pytest.raises(SystemExit) as exc:
        event_loop.run_forever()

    assert exc.value.code == 0

    # Grab the captured output of sys.stdout and sys.stderr from the capsys fixture
    out, err = capsys.readouterr()
    assert out == "Message from client: Hello!\nServer responded: Hello!\n"
