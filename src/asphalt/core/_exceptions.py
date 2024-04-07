from __future__ import annotations

from ._utils import qualified_name


class AsyncResourceError(Exception):
    """
    Raised when :meth:`Context.get_resource_nowait` received a coroutine object from a
    resource factory (the factory was asynchronous).
    """


class NoCurrentContext(Exception):
    """Raised by :func: `current_context` when there is no active context."""

    def __init__(self) -> None:
        super().__init__("There is no active context")


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
