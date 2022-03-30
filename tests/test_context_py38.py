import pytest

from asphalt.core import Dependency, inject


@pytest.mark.asyncio
async def test_dependency_injection_posonly_argument():
    async def injected(foo: int, bar: str = Dependency(), /):
        pass

    pytest.raises(TypeError, inject, injected).match(
        "Cannot inject dependency to positional-only parameter 'bar'"
    )
