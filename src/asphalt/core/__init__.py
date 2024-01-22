__all__ = (
    "ApplicationExit",
    "CLIApplicationComponent",
    "Component",
    "ContainerComponent",
    "start_background_task",
    "Context",
    "ResourceConflict",
    "ResourceEvent",
    "ResourceNotFound",
    "context_teardown",
    "current_context",
    "get_resource",
    "get_resources",
    "require_resource",
    "NoCurrentContext",
    "inject",
    "resource",
    "Event",
    "Signal",
    "stream_events",
    "wait_event",
    "run_application",
    "PluginContainer",
    "callable_name",
    "merge_config",
    "qualified_name",
)

from typing import Any

from ._component import (
    CLIApplicationComponent,
    Component,
    ContainerComponent,
)
from ._context import (
    Context,
    NoCurrentContext,
    ResourceConflict,
    ResourceEvent,
    ResourceNotFound,
    context_teardown,
    current_context,
    get_resource,
    get_resources,
    inject,
    require_resource,
    resource,
    start_background_task,
)
from ._event import Event, Signal, stream_events, wait_event
from ._exceptions import ApplicationExit
from ._runner import run_application
from ._utils import PluginContainer, callable_name, merge_config, qualified_name

# Re-export imports so they look like they live directly in this package
key: str
value: Any
for key, value in list(locals().items()):
    if getattr(value, "__module__", "").startswith("asphalt.core."):
        value.__module__ = __name__
