"""This is the change detector component for the Asphalt webnotifier tutorial."""
import asyncio
import logging

import aiohttp
from async_generator import yield_

from asphalt.core import Component, Event, Signal
from asphalt.core.context import context_teardown

logger = logging.getLogger(__name__)


class WebPageChangeEvent(Event):
    def __init__(self, source, topic, old_lines, new_lines):
        super().__init__(source, topic)
        self.old_lines = old_lines
        self.new_lines = new_lines


class Detector:
    changed = Signal(WebPageChangeEvent)

    def __init__(self, url, delay):
        self.url = url
        self.delay = delay

    async def run(self):
        with aiohttp.ClientSession() as session:
            last_modified, old_lines = None, None
            while True:
                logger.debug('Fetching contents of %s', self.url)
                headers = {'if-modified-since': last_modified} if last_modified else {}
                async with session.get(self.url, headers=headers) as resp:
                    logger.debug('Response status: %d', resp.status)
                    if resp.status == 200:
                        last_modified = resp.headers['date']
                        new_lines = (await resp.text()).split('\n')
                        if old_lines is not None and old_lines != new_lines:
                            self.changed.dispatch(old_lines, new_lines)

                        old_lines = new_lines

                await asyncio.sleep(self.delay)


class ChangeDetectorComponent(Component):
    def __init__(self, url: str, delay: int = 10):
        self.url = url
        self.delay = delay

    @context_teardown
    async def start(self, ctx):
        detector = Detector(self.url, self.delay)
        ctx.add_resource(detector, context_attr='detector')
        task = ctx.loop.create_task(detector.run())
        logging.info('Started web page change detector for url "%s" with a delay of %d seconds',
                     self.url, self.delay)

        # Can be replaced with plain "yield" on Python 3.6+
        await yield_()

        # This part is run when the context is finished
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        logging.info('Shut down web page change detector')
