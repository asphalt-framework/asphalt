# isort: off
from __future__ import annotations

import logging
from difflib import unified_diff
from typing import Any

import anyio
import httpx
from asphalt.core import CLIApplicationComponent, run_application

logger = logging.getLogger(__name__)


class ApplicationComponent(CLIApplicationComponent):
    async def run(self) -> None:
        async with httpx.AsyncClient() as http:
            last_modified, old_lines = None, None
            while True:
                logger.debug("Fetching webpage")
                headers: dict[str, Any] = (
                    {"if-modified-since": last_modified} if last_modified else {}
                )
                response = await http.get("https://imgur.com", headers=headers)
                logger.debug("Response status: %d", response.status_code)
                if response.status_code == 200:
                    last_modified = response.headers["date"]
                    new_lines = response.text.split("\n")
                    if old_lines is not None and old_lines != new_lines:
                        difference = unified_diff(old_lines, new_lines)
                        logger.info("Contents changed:\n%s", difference)

                    old_lines = new_lines

                await anyio.sleep(10)


if __name__ == "__main__":
    run_application(ApplicationComponent, logging=logging.DEBUG)
