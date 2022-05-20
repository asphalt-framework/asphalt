"""This is the root component for the Asphalt webnotifier tutorial."""
# isort: off
import logging
from difflib import HtmlDiff

from async_generator import aclosing
from asphalt.core import CLIApplicationComponent, Context, inject, resource
from asphalt.mailer.api import Mailer

from webnotifier.detector import ChangeDetectorComponent, Detector

logger = logging.getLogger(__name__)


class ApplicationComponent(CLIApplicationComponent):
    async def start(self, ctx: Context) -> None:
        self.add_component("detector", ChangeDetectorComponent)
        self.add_component("mailer", backend="smtp")
        await super().start(ctx)

    @inject
    async def run(
        self,
        ctx: Context,
        *,
        mailer: Mailer = resource(),
        detector: Detector = resource(),
    ) -> None:
        diff = HtmlDiff()
        async with aclosing(detector.changed.stream_events()) as stream:
            async for event in stream:
                difference = diff.make_file(
                    event.old_lines, event.new_lines, context=True
                )
                await mailer.create_and_deliver(
                    subject=f"Change detected in {event.source.url}",
                    html_body=difference,
                )
                logger.info("Sent notification email")
