from asphalt.core import (
    Component,
    add_resource,
    get_resource,
    get_resource_nowait,
    run_application,
)


class ParentComponent(Component):
    def __init__(self) -> None:
        self.add_component("child1", ChildComponent, name="child1")
        self.add_component("child2", ChildComponent, name="child2")

    async def prepare(self) -> None:
        print("ParentComponent.prepare()")
        add_resource("Hello")  # adds a `str` type resource by the name `default`

    async def start(self) -> None:
        print("ParentComponent.start()")
        print(get_resource_nowait(str, "child1_resource"))
        print(get_resource_nowait(str, "child2_resource"))


class ChildComponent(Component):
    parent_resource: str
    sibling_resource: str

    def __init__(self, name: str) -> None:
        self.name = name

    async def prepare(self) -> None:
        self.parent_resource = get_resource_nowait(str)
        print(f"ChildComponent.prepare() [{self.name}]")

    async def start(self) -> None:
        print(f"ChildComponent.start() [{self.name}]")

        # Add a `str` type resource, with a name like `childX_resource`
        add_resource(
            f"{self.parent_resource}, world from {self.name}!", f"{self.name}_resource"
        )

        # Do this only after adding our own resource, or we end up in a deadlock
        resource = "child1_resource" if self.name == "child2" else "child1_resource"
        await get_resource(str, resource)


run_application(ParentComponent)
