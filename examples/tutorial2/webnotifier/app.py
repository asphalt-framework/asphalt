"""This is the root component for the Asphalt webnotifier tutorial."""
import logging
from difflib import HtmlDiff

from asphalt.core import CLIApplicationComponent
from async_generator import aclosing

from webnotifier.detector import ChangeDetectorComponent

logger = logging.getLogger(__name__)


class ApplicationComponent(CLIApplicationComponent):
    async def start(self, ctx):
        self.add_component('detector', ChangeDetectorComponent)
        self.add_component('mailer', backend='smtp')
        await super().start(ctx)

    async def run(self, ctx):
        diff = HtmlDiff()
        async with aclosing(ctx.detector.changed.stream_events()) as stream:
            async for event in stream:
                difference = diff.make_file(event.old_lines, event.new_lines, context=True)
                await ctx.mailer.create_and_deliver(
                    subject='Change detected in %s' % event.source.url, html_body=difference)
                logger.info('Sent notification email')
