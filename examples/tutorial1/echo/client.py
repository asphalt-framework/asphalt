"""This is the client code for the Asphalt echo server tutorial."""

# isort: off
import sys

import anyio
from asphalt.core import CLIApplicationComponent, run_application


class ClientComponent(CLIApplicationComponent):
    def __init__(self, message: str):
        super().__init__()
        self.message = message

    async def run(self) -> None:
        async with await anyio.connect_tcp("localhost", 64100) as stream:
            await stream.send(self.message.encode() + b"\n")
            response = await stream.receive()

        print("Server responded:", response.decode().rstrip())


if __name__ == "__main__":
    component = ClientComponent(sys.argv[1])
    run_application(component)
