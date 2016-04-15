import asyncio

import pytest

from asphalt.core.component import ContainerComponent, Component, component_types
from asphalt.core.context import Context


class DummyComponent(Component):
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False

    async def start(self, ctx):
        await asyncio.sleep(0.1)
        self.started = True


@pytest.fixture(autouse=True)
def monkeypatch_plugins(monkeypatch):
    monkeypatch.setattr(component_types, '_entrypoints', {'dummy': DummyComponent})


class TestContainerComponent:
    @pytest.fixture
    def container(self):
        return ContainerComponent({'dummy': {'a': 1, 'c': 3}})

    def test_add_component(self, container):
        """
        Test that add_component works with an without an entry point and that external
        configuration overriddes directly supplied configuration values.

        """
        container.add_component('dummy', DummyComponent, a=5, b=2)

        assert len(container.child_components) == 1
        component = container.child_components['dummy']
        assert isinstance(component, DummyComponent)
        assert component.kwargs == {'a': 1, 'b': 2, 'c': 3}

    @pytest.mark.parametrize('alias, cls, exc_cls, message', [
        ('', None, TypeError, 'component_alias must be a nonempty string'),
        ('foo', None, LookupError, 'no such entry point in asphalt.components: foo'),
        ('foo', int, TypeError,
         'int is not a subclass of asphalt.core.component.Component')
    ], ids=['empty_alias', 'bogus_entry_point', 'wrong_subclass'])
    def test_add_component_errors(self, container, alias, cls, exc_cls, message):
        exc = pytest.raises(exc_cls, container.add_component, alias, cls)
        assert str(exc.value) == message

    def test_add_duplicate_component(self, container):
        container.add_component('dummy')
        exc = pytest.raises(ValueError, container.add_component, 'dummy')
        assert str(exc.value) == 'there is already a child component named "dummy"'

    @pytest.mark.asyncio
    async def test_start(self, container):
        await container.start(Context())
        assert container.child_components['dummy'].started
