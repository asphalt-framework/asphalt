from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ._utils import format_component_name, qualified_name

if TYPE_CHECKING:
    from ._component import Component


class AsyncResourceError(Exception):
    """
    Raised when :meth:`Context.get_resource_nowait` received a coroutine object from a
    resource factory (the factory was asynchronous).
    """


class ComponentStartError(Exception):
    """
    Raised by :func:`start_component` when there's an error instantiating or starting a
    component.

    .. note:: The underlying exception can be retrieved from the ``__cause__``
        attribute.

    :ivar Literal["creating", "preparing", "starting"] phase: the phase of the component
        initialization in which the error occurred
    :ivar str path: the path of the component in the configuration (an empty string if
        this is the root component)
    :ivar type[Component] component_type: the component class
    """

    def __init__(
        self,
        phase: Literal["creating", "preparing", "starting"],
        path: str,
        component_type: type[Component],
    ):
        super().__init__(phase, path, component_type)
        self.phase = phase
        self.path = path
        self.component_type = component_type

    def __str__(self) -> str:
        exc_msg = qualified_name(self.__cause__)
        if exc_str := str(self.__cause__):
            exc_msg += f": {exc_str}"

        formatted_name = format_component_name(self.path, self.component_type)
        return f"error {self.phase} {formatted_name}: {exc_msg}"


class NoCurrentContext(Exception):
    """Raised by :func: `current_context` when there is no active context."""

    def __init__(self) -> None:
        super().__init__("there is no active context")


class ResourceConflict(Exception):
    """
    Raised when a new resource that is being published conflicts with an existing
    resource or context variable.
    """


class ResourceNotFound(LookupError):
    """Raised when a resource request cannot be fulfilled within the allotted time."""

    def __init__(self, type: type, name: str) -> None:
        super().__init__(type, name)
        self.type = type
        self.name = name

    def __str__(self) -> str:
        return (
            f"no matching resource was found for type={qualified_name(self.type)} "
            f"name={self.name!r}"
        )
