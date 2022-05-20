"""This is the client code for the Asphalt echo server tutorial."""
import sys
from asyncio import open_connection

from asphalt.core import CLIApplicationComponent, Context, run_application


class ClientComponent(CLIApplicationComponent):
    def __init__(self, message: str):
        super().__init__()
        self.message = message

    async def run(self, ctx: Context) -> None:
        reader, writer = await open_connection("localhost", 64100)
        writer.write(self.message.encode() + b"\n")
        response = await reader.readline()
        writer.close()
        print("Server responded:", response.decode().rstrip())


if __name__ == "__main__":
    component = ClientComponent(sys.argv[1])
    run_application(component)
