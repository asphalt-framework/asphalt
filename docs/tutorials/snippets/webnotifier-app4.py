# isort: off
from __future__ import annotations

import logging
from difflib import HtmlDiff
from typing import Any

import anyio
import httpx
from asphalt.core import CLIApplicationComponent, run_application
from asphalt.core import inject, resource
from asphalt.mailer import Mailer

logger = logging.getLogger(__name__)


class ApplicationComponent(CLIApplicationComponent):
    def __init__(self) -> None:
        self.add_component(
            "mailer",
            backend="smtp",
            host="your.smtp.server.here",
            message_defaults={"sender": "your@email.here", "to": "your@email.here"},
        )

    @inject
    async def run(self, *, mailer: Mailer = resource()) -> None:
        async with httpx.AsyncClient() as http:
            last_modified, old_lines = None, None
            diff = HtmlDiff()
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
                        difference = diff.make_file(old_lines, new_lines, context=True)
                        await mailer.create_and_deliver(
                            subject="Change detected in web page", html_body=difference
                        )
                        logger.info("Sent notification email")

                    old_lines = new_lines

                await anyio.sleep(10)


if __name__ == "__main__":
    run_application(ApplicationComponent(), logging=logging.DEBUG)
