.. image:: https://github.com/asphalt-framework/asphalt/actions/workflows/test.yml/badge.svg
  :target: https://github.com/asphalt-framework/asphalt/actions/workflows/test.yml
  :alt: Build Status
.. image:: https://coveralls.io/repos/github/asphalt-framework/asphalt/badge.svg?branch=master
  :target: https://coveralls.io/github/asphalt-framework/asphalt?branch=master
  :alt: Code Coverage
.. image:: https://readthedocs.org/projects/asphalt/badge/?version=latest
  :target: https://asphalt.readthedocs.io/en/latest/?badge=latest
  :alt: Documentation Status

Asphalt is an asyncio_ based microframework for network oriented applications.

Its highlight features are:

* An ecosystem of components for integrating functionality from third party libraries and external
  services
* A configuration system where hard-coded defaults can be selectively overridden by external
  configuration
* A sophisticated signal system that lets you connect different services to create complex
  event-driven interactions
* Supports uvloop_ and tokio_ as event loop policy providers (though YMMV with the last one)
* Elegant handling of blocking APIs through the use of thread pooling
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

.. _asyncio: https://docs.python.org/3/library/asyncio.html
.. _uvloop: https://github.com/MagicStack/uvloop
.. _tokio: https://github.com/PyO3/tokio
.. _Type hints: https://www.python.org/dev/peps/pep-0484/
.. _semantic versioning: http://semver.org/
