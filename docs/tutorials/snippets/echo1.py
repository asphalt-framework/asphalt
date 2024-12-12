# isort: off
from __future__ import annotations

from asphalt.core import Component, run_application


class ServerComponent(Component):
    async def start(self) -> None:
        print("Hello, world!")


if __name__ == "__main__":
    run_application(ServerComponent)
