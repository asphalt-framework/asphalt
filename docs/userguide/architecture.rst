Architectural overview
======================

Asphalt applications are built by assembling a hiearchy of *components*, starting from one
*root component* which may contain subcomponents which in turn may contain their own subcomponents
and so on. Each component provides some specific functionality for the application.
Some components could come from third party libraries while some components could be part
of your own application. Typically you will write the root component yourself, but depending on the
application, this may not be necessary.

Components work together through a *context*. Each Asphalt application has at least one context
object, created by the application runner. This context is passed to the
:meth:`~asphalt.core.Component.start` method of the component.
The context is used to share *resources* between the components. Resources can be any arbitrary
objects like data or services (such as database connections). Each resource is associated with a
textual type (usually the fully qualified class name of the resource object). Components can
publish and request resources, like merchants in a marketplace.

Contexts can have subcontexts. How and if subcontexts are used dependes on the components using
them. For example, a component serving network requests will want to create a subcontext for each
request it handles to store request specific information and other state. While the subcontext will
have its own independent state, it also has full access the resources of its parent context.

An Asphalt application is normally started by the *runner*. The runner is a function that
initializes the logging system, the event loop, the top level context and then starts the root
component. The root component will start any subcomponents and so on. Finally, the runner will just
keep the event loop running until the application is shut down.

The runner is further extended by the ``asphalt`` command line tool which can read a YAML formatted
configuration file and pass the parsed configuration to the runner. This allows applications to
have different development and production configurations without having to modify any actual code.
