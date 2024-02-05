from typing import Any

from ._component import CLIApplicationComponent as CLIApplicationComponent
from ._component import Component as Component
from ._component import ContainerComponent as ContainerComponent
from ._context import Context as Context
from ._context import GeneratedResource as GeneratedResource
from ._context import NoCurrentContext as NoCurrentContext
from ._context import ResourceConflict as ResourceConflict
from ._context import ResourceEvent as ResourceEvent
from ._context import ResourceNotFound as ResourceNotFound
from ._context import add_resource as add_resource
from ._context import add_resource_factory as add_resource_factory
from ._context import add_teardown_callback as add_teardown_callback
from ._context import context_teardown as context_teardown
from ._context import current_context as current_context
from ._context import get_resource as get_resource
from ._context import get_resources as get_resources
from ._context import inject as inject
from ._context import request_resource as request_resource
from ._context import require_resource as require_resource
from ._context import resource as resource
from ._context import start_background_task as start_background_task
from ._event import Event as Event
from ._event import Signal as Signal
from ._event import stream_events as stream_events
from ._event import wait_event as wait_event
from ._exceptions import ApplicationExit as ApplicationExit
from ._runner import run_application as run_application
from ._utils import PluginContainer as PluginContainer
from ._utils import callable_name as callable_name
from ._utils import merge_config as merge_config
from ._utils import qualified_name as qualified_name
from ._utils import resolve_reference as resolve_reference

# Re-export imports so they look like they live directly in this package
key: str
value: Any
for key, value in list(locals().items()):
    if getattr(value, "__module__", "").startswith("asphalt.core."):
        value.__module__ = __name__
