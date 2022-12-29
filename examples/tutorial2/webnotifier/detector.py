"""This is the change detector component for the Asphalt webnotifier tutorial."""
from __future__ import annotations

from collections.abc import AsyncGenerator

# isort: off
import logging
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
from asphalt.core import (
    Component,
    Context,
    Event,
    Signal,
    context_teardown,
    start_service_task,
)

logger = logging.getLogger(__name__)


@dataclass
class WebPageChangeEvent(Event):
    old_lines: list[str]
    new_lines: list[str]


class Detector:
    changed = Signal(WebPageChangeEvent)

    def __init__(self, url: str, delay: float):
        self.url = url
        self.delay = delay

    async def run(self) -> None:
        async with httpx.AsyncClient() as http:
            last_modified, old_lines = None, None
            while True:
                logger.debug("Fetching contents of %s", self.url)
                headers: dict[str, Any] = (
                    {"if-modified-since": last_modified} if last_modified else {}
                )
                resp = await http.get(self.url, headers=headers)
                logger.debug("Response status: %d", resp.status_code)
                if resp.status_code == 200:
                    last_modified = resp.headers["date"]
                    new_lines = resp.text.split("\n")
                    if old_lines is not None and old_lines != new_lines:
                        await self.changed.dispatch(
                            WebPageChangeEvent(old_lines, new_lines)
                        )

                    old_lines = new_lines

                await anyio.sleep(self.delay)


class ChangeDetectorComponent(Component):
    def __init__(self, url: str, delay: int = 10):
        self.url = url
        self.delay = delay

    @context_teardown
    async def start(self, ctx: Context) -> AsyncGenerator[None, Exception | None]:
        detector = Detector(self.url, self.delay)
        await ctx.add_resource(detector)
        start_service_task(detector.run, "Web page change detector")
        logging.info(
            'Started web page change detector for url "%s" with a delay of %d seconds',
            self.url,
            self.delay,
        )

        yield

        # This part is run when the context is finished
        logger.info("Shut down web page change detector")
