"""This is the server code for the Asphalt echo server tutorial."""
from collections.abc import AsyncIterator

import anyio
from anyio.abc import SocketStream

from asphalt.core import (
    Component,
    Context,
    context_teardown,
    run_application,
    start_service_task,
)


async def handle(stream: SocketStream) -> None:
    message = await stream.receive()
    await stream.send(message)
    print("Message from client:", message.decode().rstrip())


class ServerComponent(Component):
    @context_teardown
    async def start(self, ctx: Context) -> AsyncIterator[None]:
        async with await anyio.create_tcp_listener(
            local_host="localhost", local_port=64100
        ) as listener:
            start_service_task(lambda: listener.serve(handle), "Echo server")
            yield


if __name__ == "__main__":
    component = ServerComponent()
    anyio.run(run_application, component)
