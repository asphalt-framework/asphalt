import asyncio

import pytest
from asphalt.core import Context

from echo.client import ClientComponent
from echo.server import ServerComponent


@pytest.fixture
def event_loop():
    # Required on pytest-asyncio v0.4.0 and newer since the event_loop fixture provided by the
    # plugin no longer sets the global event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
def context(event_loop):
    ctx = Context()
    yield ctx
    event_loop.run_until_complete(ctx.finished.dispatch(None, return_future=True))


@pytest.fixture
def server_component(event_loop, context):
    component = ServerComponent()
    event_loop.run_until_complete(component.start(context))


def test_client(event_loop, server_component, context, capsys):
    client = ClientComponent('Hello!')
    event_loop.run_until_complete(client.start(context))
    exc = pytest.raises(SystemExit, event_loop.run_forever)
    assert exc.value.code == 0

    # Grab the captured output of sys.stdout and sys.stderr from the capsys fixture
    out, err = capsys.readouterr()
    assert out == 'Message from client: Hello!\nServer responded: Hello!\n'
