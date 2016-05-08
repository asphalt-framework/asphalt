"""This is the change detector component for the Asphalt webnotifier tutorial."""
import asyncio
import logging
from asyncio.events import get_event_loop

import aiohttp
from typeguard import check_argument_types

from asphalt.core import Component, Event, Signal

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
        assert check_argument_types()
        self.url = url
        self.delay = delay

    async def start(self, ctx):
        def shutdown(event):
            task.cancel()
            logging.info('Shut down web page change detector')

        detector = Detector(self.url, self.delay)
        ctx.publish_resource(detector, context_attr='detector')
        task = get_event_loop().create_task(detector.run())
        ctx.finished.connect(shutdown)
        logging.info('Started web page change detector for url "%s" with a delay of %d seconds',
                     self.url, self.delay)
