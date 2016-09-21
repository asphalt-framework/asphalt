"""This is the client code for the Asphalt echo server tutorial."""
import sys
from asyncio import open_connection

from asphalt.core import CLIApplicationComponent, run_application


class ClientComponent(CLIApplicationComponent):
    async def run(self, ctx):
        message = sys.argv[1]
        reader, writer = await open_connection('localhost', 64100)
        writer.write(message.encode() + b'\n')
        response = await reader.readline()
        writer.close()
        print('Server responded:', response.decode().rstrip())

if __name__ == '__main__':
    component = ClientComponent()
    run_application(component)
