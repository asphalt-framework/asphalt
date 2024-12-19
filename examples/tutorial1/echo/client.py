"""This is the client code for the Asphalt echo server tutorial."""

# isort: off
import sys
from dataclasses import dataclass

import anyio
from asphalt.core import CLIApplicationComponent, run_application


@dataclass
class ClientComponent(CLIApplicationComponent):
    message: str

    async def run(self) -> None:
        async with await anyio.connect_tcp("localhost", 64100) as stream:
            await stream.send(self.message.encode() + b"\n")
            response = await stream.receive()

        print("Server responded:", response.decode().rstrip())


if __name__ == "__main__":
    run_application(ClientComponent, {"message": sys.argv[1]})
