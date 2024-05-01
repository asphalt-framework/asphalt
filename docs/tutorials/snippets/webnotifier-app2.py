# isort: off
from __future__ import annotations

import logging
from typing import Any

import anyio
import httpx
from asphalt.core import CLIApplicationComponent, run_application

logger = logging.getLogger(__name__)


class ApplicationComponent(CLIApplicationComponent):
    async def run(self) -> None:
        last_modified = None
        async with httpx.AsyncClient() as http:
            while True:
                headers: dict[str, Any] = (
                    {"if-modified-since": last_modified} if last_modified else {}
                )
                response = await http.get("https://imgur.com", headers=headers)
                logger.debug("Response status: %d", response.status_code)
                if response.status_code == 200:
                    last_modified = response.headers["date"]
                    logger.info("Contents changed")

                await anyio.sleep(10)


if __name__ == "__main__":
    run_application(ApplicationComponent(), logging=logging.DEBUG)
