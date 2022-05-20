"""This is the server code for the Asphalt echo server tutorial."""
from asyncio import StreamReader, StreamWriter, start_server

from asphalt.core import Component, Context, run_application


async def client_connected(reader: StreamReader, writer: StreamWriter) -> None:
    message = await reader.readline()
    writer.write(message)
    writer.close()
    print("Message from client:", message.decode().rstrip())


class ServerComponent(Component):
    async def start(self, ctx: Context) -> None:
        await start_server(client_connected, "localhost", 64100)


if __name__ == "__main__":
    component = ServerComponent()
    run_application(component)
