Tutorial 2: Something a little more practical – a web page change detector
==========================================================================

Now that you've gone through the basics of creating an Asphalt application, it's time to expand
your horizons a little. In this tutorial you will learn to use a container component to create
a multi-component application and how to set up a configuration file for that.

The application you will build this time will periodically load a web page and see if it has
changed since the last check. When changes are detected, it will then present the user with the
computed differences between the old and the new versions.

Setting up the project structure
--------------------------------

As in the previous tutorial, you will need a project directory and a virtual environment. Create a
directory named ``tutorial2`` and make a new virtual environment inside it. Then activate it and
use ``pip`` to install the ``asphalt-mailer`` and ``aiohttp`` libraries:

.. code-block:: bash

    pip install asphalt-mailer aiohttp

This will also pull in the core Asphalt library as a dependency.

Next, create a package directory named ``webnotifier``. The code in the following sections should
be put in its ``__init__.py`` file (unless explicitly stated otherwise).

Detecting changes in a web page
-------------------------------

The first task is to set up a loop that periodically retrieves the web page. For that, you can
adapt code from the `aiohttp HTTP client tutorial`_::

    import asyncio
    import logging

    import aiohttp
    from asphalt.core import Component

    logger = logging.getLogger(__name__)


    class ApplicationComponent(Component):
        async def start(ctx):
            with aiohttp.ClientSession() as session:
                while True:
                    async with session.get('http://imgur.com') as resp:
                        await resp.text()

                await asyncio.sleep(10)

    if __name__ == '__main__':
        run_application(ApplicationComponent(), logging=logging.DEBUG)

Great, so now the code fetches the contents of ``http://imgur.com`` at 10 second intervals.
But this isn't very useful yet – you need something that compares the old and new versions of the
contents somehow. Furthermore, constantly loading the contents of a page exerts unnecessary strain
on the hosting provider. We want our application to be as polite and efficient as reasonably
possible.

To that end, you can use the ``if-modified-since`` header in the request. If the requests after the
initial one specify the last modified date value in the request headers, the remote server will
respond with a ``304 Not Modified`` if the contents have not changed since that moment.

So, modify the code as follows::

    class ApplicationComponent(Component):
        async def start(ctx):
            last_modified = None
            with aiohttp.ClientSession() as session:
                while True:
                    headers = {'if-modified-since': last_modified} if last_modified else {}
                    async with session.get('http://imgur.com', headers=headers) as resp:
                        logger.debug('Response status: %d', resp.status)
                        if resp.status == 200:
                            last_modified = resp.headers['date']
                            await resp.text()
                            logger.info('Contents changed')

                await asyncio.sleep(10)

The code here stores the ``date`` header from the first response and uses it in the
``if-modified-since`` header of the next request. A ``200`` response indicates that the web page
has changed so the last modified date is updated and the contents are retrieved from the response.
Some logging calls were also sprinkled in the code to give you an idea of what's happening.

.. _aiohttp HTTP client tutorial: http://aiohttp.readthedocs.io/en/stable/client.html

Computing the changes between old and new versions
--------------------------------------------------

Now you have code that actually detects when the page has been modified between the requests.
But it doesn't yet show *what* in its contents has changed. The next step will then be to use the
standard library :mod:`difflib` module to calculate the difference between the contents and send it
to the logger::

    from difflib import unified_diff


    class ApplicationComponent(Component):
        async def start(self, ctx):
            with aiohttp.ClientSession() as session:
                last_modified, old_lines = None, None
                while True:
                    logger.debug('Fetching webpage')
                    headers = {'if-modified-since': last_modified} if last_modified else {}
                    async with session.get('http://imgur.com', headers=headers) as resp:
                        logger.debug('Response status: %d', resp.status)
                        if resp.status == 200:
                            last_modified = resp.headers['date']
                            new_lines = (await resp.text()).split('\n')
                            if old_lines is not None and old_lines != new_lines:
                                difference = '\n'.join(unified_diff(old_lines, new_lines))
                                logger.info('Contents changed:\n%s', difference)

                            old_lines = new_lines

                    await asyncio.sleep(10)

This modified code now stores the old and new contents in different variables to enable them to be
compared. The ``.split('\n')`` is needed because :func:`~difflib.unified_diff` requires the input
to be iterables of strings. Likewise, the ``'\n'.join(...)`` is necessary because the output is
also an iterable of strings.

Sending changes via email
-------------------------

While an application that logs the changes on the console could be useful on its own, it'd be much
better if it actually notified the user by means of some communication medium, wouldn't it?
For this specific purpose you need the ``asphalt-mailer`` library you installed in the beginning.
The next modification will send the HTML formatted differences to you by email.

But, you only have a single component in your app now. To use ``asphalt-mailer``, you will need to
add its component to your application somehow. Enter
:class:`~asphalt.core.component.ContainerComponent`. With that, you can create a hierarchy of
components where the ``mailer`` component is a child component of your own container component.

And to make the the results look nicer in an email message, you can switch to using
:class:`difflib.HtmlDiff` to produce the delta output::

    from difflib import HtmlDiff

    from asphalt.core import ContainerComponent


    class ApplicationComponent(ContainerComponent):
        async def start(self, ctx):
            self.add_component(
                'mailer', backend='smtp', host='your.smtp.server.here',
                message_defaults={'sender': 'your@email.here', 'to': 'your@email.here'})
            await super().start(ctx)

            with aiohttp.ClientSession() as session:
                last_modified, old_lines = None, None
                diff = HtmlDiff()
                while True:
                    logger.debug('Fetching webpage')
                    headers = {'if-modified-since': last_modified} if last_modified else {}
                    async with session.get('http://imgur.com', headers=headers) as resp:
                        logger.debug('Response status: %d', resp.status)
                        if resp.status == 200:
                            last_modified = resp.headers['date']
                            new_lines = (await resp.text()).split('\n')
                            if old_lines is not None and old_lines != new_lines:
                                difference = diff.make_file(old_lines, new_lines, context=True)
                                logger.info('Sent notification email')

                            old_lines = new_lines

                    await asyncio.sleep(10)

You'll need to replace the ``host``, ``sender`` and ``to`` arguments for the mailer component and
possibly add the ``ssl``, ``username`` and ``password`` arguments if your SMTP server requires
authentication.

With these changes, you'll get a new HTML formatted email each time the code detects changes in the
target web page.

Separating the change detection logic
-------------------------------------

While the application now works as intended, you're left with two small problems. First off, the
target URL and checking frequency are hard coded. That is, they can only be changed by modifying
the program code. It is not reasonable to expect non-technical users to modify the code when they
want to simply change the target website or the frequency of checks. Second, the change detection
logic is hardwired to the notification code. A well designed application should maintain proper
`separation of concerns`_. One way to do this is to separate the change detection logic to its own
class.

Create a new module named ``detector`` in the ``webnotifier`` package. Then, add the change event
class to it::

    import asyncio
    import logging
    from asyncio.events import get_event_loop

    import aiohttp

    from asphalt.core import Component, Event, Signal

    logger = logging.getLogger(__name__)


    class WebPageChangeEvent(Event):
        def __init__(self, source, topic, old_lines, new_lines):
            super().__init__(source, topic)
            self.old_lines = old_lines
            self.new_lines = new_lines

This class defines the type of event that the notifier will emit when the target web page changes.
The old and new content are stored in the event instance to allow the event listener to generate
the output any way it wants.

Next, add another class in the same module that will do the HTTP requests and change detection::

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

The constructor arguments allow you to freely specify the parameters for the detection process.
The class includes a signal named ``change`` that uses the previously created
``WebPageChangeEvent`` class. The code dispatches such an event when a change in the target web
page is detected.

Finally, add the component class which will allow you to integrate this functionality into any
Asphalt application::

    class ChangeDetectorComponent(Component):
        def __init__(self, url, delay=10):
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

The component's ``start()`` method starts the detector's ``run()`` method as a new task, publishes
the detector object as resource and installs an event listener that will shut down the detector
when the context finishes.

Now that you've moved the change detection code to its own module, ``ApplicationComponent`` will
become somewhat lighter::

    class ApplicationComponent(ContainerComponent):
        async def start(self, ctx):
            self.add_component('detector', ChangeDetectorComponent, url='http://imgur.com')
            self.add_component(
                'mailer', backend='smtp', host='your.smtp.server.here',
                message_defaults={'sender': 'your@email.here', 'to': 'your@email.here'})
            await super().start(ctx)

            diff = HtmlDiff()
            async for event in ctx.detector.changed.stream_events():
                difference = diff.make_file(event.old_lines, event.new_lines, context=True)
                await ctx.mailer.create_and_deliver(
                    subject='Change detected in %s' % event.source.url, html_body=difference)
                logger.info('Sent notification email')

The main application component will now use the detector resource published by
``ChangeDetectorComponent``. It adds one event listener which reacts to change events by creating
an HTML formatted difference and sending it to the default recipient.

Once the ``start()`` method here has run to completion, the event loop finally has a chance to run
the task created for ``Detector.run()``. This will allow the detector to do its work and dispatch
those ``changed`` events that the ``page_changed()`` listener callback expects.

.. _separation of concerns: https://en.wikipedia.org/wiki/Separation_of_concerns

Setting up the configuration file
---------------------------------

Now that your application code is in good shape, you will need to give the user an easy way to
configure it. This is where YAML_ configuration files come in handy. They're clearly structured and
are far less intimidating to end users than program code. And you can also have more than one of
them, in case you want to run the program with a different configuration.

In your project directory, create a file named ``config.yaml`` with the following contents:

.. code-block:: yaml

    ---
    component:
      type: webnotifier:ApplicationComponent
      components:
        detector:
          url: http://imgur.com/
          delay: 15
        mailer:
          host: your.smtp.server.here
          message_defaults:
            sender: your@email.here
            to: your@email.here

    logging:
      version: 1
      disable_existing_loggers: false
      formatters:
        default:
          format: '[%(asctime)s %(levelname)s] %(message)s'
      handlers:
        console:
          class: logging.StreamHandler
          formatter: default
      loggers:
        root:
          handlers: [console]
          level: INFO
        webnotifier:
          handlers: [console]
          level: DEBUG
          propagate: false

The ``component`` section defines parameters for the root component. Aside from the special
``type`` key which tells the runner where to find the component class, all the keys in this section
are passed to the constructor of ``ApplicationComponent`` as keyword arguments. Keys under
``components`` will match the alias of each child component, which is given as the first argument
to :meth:`~asphalt.core.component.ContainerComponent.add_component`. Any component parameters given
here can now be removed from the ``add_component()`` call in ``ApplicationComponent``'s code.

The logging configuration here sets up two loggers, one for ``webnotifier`` and its descendants
and another (``root``) as a catch-all for everything else. It specifies one handler that just
writes all log entries to the standard output. To learn more about what you can do with the logging
configuration, consult the :ref:`python:logging-config-dictschema` section in the standard library
documentation.

You can now run your app with the ``asphalt run`` command, provided that the project directory is
on Python's search path. When your application is `properly packaged`_ and installed in
``site-packages``, this won't be a problem. But for the purposes of this tutorial, you can
temporarily add it to the search path by setting the ``PYTHONPATH`` environment variable:

.. code-block:: bash

    PYTHONPATH=. asphalt run config.yaml

On Windows:

.. code-block:: doscon

    set PYTHONPATH=%CD%
    asphalt run config.yaml

.. note::
    The ``if __name__ == '__main__':`` block is no longer needed since ``asphalt run`` is now used
    as the entry point for the application.

.. _YAML: http://yaml.org/
.. _properly packaged: https://packaging.python.org/

Conclusion
----------

You now know how to take advantage of Asphalt's component system to add structure to your
application. You've learned how to build reusable components and how to make the components work
together through the use of resources. Last, but not least, you've learned to set up a YAML
configuration file for your application and to set up a fine grained logging configuration in it.

You now possess enough knowledge to leverage Asphalt to create practical applications. You are now
encouraged to find out what `Asphalt component projects`_ exist to aid your application
development. Happy coding ☺

.. _Asphalt component projects: https://github.com/asphalt-framework
