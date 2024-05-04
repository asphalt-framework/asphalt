"""This is the root component for the Asphalt webnotifier tutorial."""

# isort: off
from __future__ import annotations

import logging
from difflib import HtmlDiff
from typing import Any

from asphalt.core import CLIApplicationComponent, inject, resource
from asphalt.mailer import Mailer

from webnotifier.detector import ChangeDetectorComponent, Detector

logger = logging.getLogger(__name__)


class ApplicationComponent(CLIApplicationComponent):
    def __init__(
        self, components: dict[str, dict[str, Any] | None] | None = None
    ) -> None:
        self.add_component("detector", ChangeDetectorComponent)
        self.add_component("mailer", backend="smtp")
        super().__init__(components)

    @inject
    async def run(
        self,
        *,
        mailer: Mailer = resource(),
        detector: Detector = resource(),
    ) -> None:
        diff = HtmlDiff()
        async with detector.changed.stream_events() as stream:
            async for event in stream:
                difference = diff.make_file(
                    event.old_lines, event.new_lines, context=True
                )
                await mailer.create_and_deliver(
                    subject=f"Change detected in {event.source.url}",
                    html_body=difference,
                )
                logger.info("Sent notification email")
