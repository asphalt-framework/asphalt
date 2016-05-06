"""This is the root component for the Asphalt webnotifier tutorial."""
import logging
from difflib import HtmlDiff

from asphalt.core import ContainerComponent

from webnotifier.detector import ChangeDetectorComponent

logger = logging.getLogger(__name__)


class ApplicationComponent(ContainerComponent):
    async def start(self, ctx):
        self.add_component('detector', ChangeDetectorComponent)
        self.add_component('mailer', backend='smtp')
        await super().start(ctx)

        async def page_changed(event):
            difference = diff.make_file(event.old_lines, event.new_lines, context=True)
            await ctx.mailer.create_and_deliver(subject='Change detected in %s ' % event.url,
                                                html_body=difference)
            logger.info('Sent email with HTML changes')

        diff = HtmlDiff()
        ctx.detector.add_listener('changed', page_changed)
