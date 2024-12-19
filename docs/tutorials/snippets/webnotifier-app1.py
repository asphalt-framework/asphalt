# isort: off
import logging

import anyio
import httpx
from asphalt.core import CLIApplicationComponent, run_application

logger = logging.getLogger(__name__)


class ApplicationComponent(CLIApplicationComponent):
    async def run(self) -> None:
        async with httpx.AsyncClient() as http:
            while True:
                await http.get("https://imgur.com")
                await anyio.sleep(10)


if __name__ == "__main__":
    run_application(ApplicationComponent, logging=logging.DEBUG)
