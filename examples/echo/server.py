"""This is the server code for the Asphalt echo server tutorial."""
from asyncio import start_server

from asphalt.core import Component, run_application


async def client_connected(reader, writer):
    message = await reader.readline()
    writer.write(message)
    writer.close()
    print('Message from client:', message.decode().rstrip())


class ServerComponent(Component):
    async def start(self, ctx):
        await start_server(client_connected, 'localhost', 64100)

if __name__ == '__main__':
    component = ServerComponent()
    run_application(component)
