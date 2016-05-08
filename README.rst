.. image:: https://travis-ci.org/asphalt-framework/asphalt.svg?branch=master
  :target: https://travis-ci.org/asphalt-framework/asphalt
  :alt: Build Status
.. image:: https://coveralls.io/repos/asphalt-framework/asphalt/badge.svg?branch=master&service=github
  :target: https://coveralls.io/github/asphalt-framework/asphalt?branch=master
  :alt: Code Coverage

Asphalt is an asyncio_ based microframework for network oriented applications.

Its highlight features are:

* An ecosystem of components for integrating functionality from third party libraries and external
  services
* A configuration system where hard-coded defaults can be selectively overridden by external
  configuration
* A sophisticated signal system that lets you connect different services to create complex
  event-driven interactions
* Supports uvloop_ and aiogevent_ as event loop policy providers (though YMMV with the latter one)
* Elegant handling of blocking APIs through the use of `asyncio_extras`_
* Run time type checking for development and testing to fail early when functions are called with
  incompatible arguments (can be disabled with **zero** overhead for production deployments!)
* `Type hints`_ and `semantic versioning`_ used throughout the core and all component libraries

Asphalt can be used to make any imaginable kind of networked application, ranging from trivial
command line tools to highly complex component hierarchies that start multiple network servers
and/or clients using different protocols.

What really sets Asphalt apart from other frameworks is its resource sharing system – the kind of
functionality usually only found in bulky application server software. Asphalt components publish
their services as *resources* in a shared *context*. Components can build on resources provided by
each other, making it possible to create components that offer highly sophisticated functionality
with relatively little effort.

Project links
-------------

* `Component projects`_
* `Documentation`_
* `Help and support`_
* `Source code`_
* `Issue tracker`_

.. _asyncio: https://docs.python.org/3/library/asyncio.html
.. _uvloop: https://github.com/MagicStack/uvloop
.. _aiogevent: https://bitbucket.org/haypo/aiogevent
.. _asyncio_extras: https://github.com/agronholm/asyncio_extras
.. _Type hints: https://www.python.org/dev/peps/pep-0484/
.. _semantic versioning: http://semver.org/
.. _Component projects: https://github.com/asphalt-framework
.. _Documentation: http://asphalt.readthedocs.org/en/latest/
.. _Help and support: https://github.com/asphalt-framework/asphalt/wiki/Help-and-support
.. _Source code: https://github.com/asphalt-framework/asphalt
.. _Issue tracker: https://github.com/asphalt-framework/asphalt/issues
