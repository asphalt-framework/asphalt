"""This is the client code for the Asphalt echo server tutorial."""
import sys
from asyncio import get_event_loop, open_connection

from asphalt.core import Component, run_application


class ClientComponent(Component):
    def __init__(self, message: str):
        self.message = message

    async def start(self, ctx):
        reader, writer = await open_connection('localhost', 64100)
        writer.write(self.message.encode() + b'\n')
        response = await reader.readline()
        writer.close()
        print('Server responded:', response.decode().rstrip())
        get_event_loop().stop()

if __name__ == '__main__':
    msg = sys.argv[1]
    component = ClientComponent(msg)
    run_application(component)
