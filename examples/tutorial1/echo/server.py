"""This is the server code for the Asphalt echo server tutorial."""

# isort: off
from __future__ import annotations

import anyio
from anyio.abc import SocketStream, TaskStatus
from asphalt.core import Component, run_application, start_service_task


async def handle(stream: SocketStream) -> None:
    message = await stream.receive()
    await stream.send(message)
    print("Message from client:", message.decode().rstrip())


async def serve_requests(*, task_status: TaskStatus[None]) -> None:
    async with await anyio.create_tcp_listener(
        local_host="localhost", local_port=64100
    ) as listener:
        task_status.started()
        await listener.serve(handle)


class ServerComponent(Component):
    async def start(self) -> None:
        await start_service_task(serve_requests, "Echo server")


if __name__ == "__main__":
    run_application(ServerComponent)
