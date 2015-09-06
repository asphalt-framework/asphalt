from asyncio import coroutine

import pytest

from asphalt.core.context import (ApplicationContext, TransportContext, HandlerContext,
                                  ContextScope)
from asphalt.core.resource import ResourceConflict, ResourceNotFoundError
from asphalt.core.router import Endpoint


@pytest.fixture
def app_context():
    return ApplicationContext({'setting': 'blah'})


@pytest.fixture
def transport_context(app_context):
    return TransportContext(app_context)


@pytest.fixture
def handler_context(transport_context):
    endpoint = Endpoint(lambda ctx: None)
    return HandlerContext(transport_context, endpoint)


class TestApplicationContext:
    def test_add_lazy_resource(self, app_context):
        """Tests that lazy resources are only created once per context instance."""

        def creator(ctx):
            nonlocal value
            assert ctx is app_context
            value += 1
            return value

        value = 0
        app_context.add_lazy_property(app_context.scope, 'foo', creator)
        assert app_context.foo == 1
        assert app_context.foo == 1
        assert 'foo' in app_context.__dict__

    def test_resource_added_removed(self, app_context):
        """
        Tests that when resources are added, they are also set as properties of the application
        context. Likewise, when they are removed, they are deleted from the application context.
        """

        resource = app_context.resources.add(1, context_var='foo')
        assert app_context.foo == 1
        app_context.resources.remove(resource)
        assert 'foo' not in app_context.__dict__

    @pytest.mark.asyncio
    def test_add_member_conflicting_resource(self, app_context):
        app_context.a = 2
        exc = pytest.raises(ResourceConflict, app_context.resources.add, 2, context_var='a')
        assert str(exc.value) == (
            "Resource(types=('int',), alias='default', value=2, context_var='a') "
            "conflicts with an application context property")

        with pytest.raises(ResourceNotFoundError):
            yield from app_context.resources.request(int, timeout=0)

    @pytest.mark.asyncio
    def test_add_lazy_property_conflicting_resource(self, app_context: ApplicationContext):
        app_context.add_lazy_property(ContextScope.application, 'a', lambda ctx: 2)
        exc = pytest.raises(ResourceConflict, app_context.resources.add, 2, context_var='a')
        assert str(exc.value) == (
            "Resource(types=('int',), alias='default', value=2, context_var='a') "
            "conflicts with an application scoped lazy property")

        with pytest.raises(ResourceNotFoundError):
            yield from app_context.resources.request(int, timeout=0)

    def test_lazy_property_coroutine(self, app_context: ApplicationContext):
        exc = pytest.raises(AssertionError, app_context.add_lazy_property,
                            ContextScope.application, 'foo', coroutine(lambda ctx: None))
        assert str(exc.value) == 'creator cannot be a coroutine function'

    def test_lazy_property_duplicate(self, app_context: ApplicationContext):
        app_context.add_lazy_property(ContextScope.application, 'foo', lambda ctx: None)
        exc = pytest.raises(ValueError, app_context.add_lazy_property, ContextScope.application,
                            'foo', lambda ctx: None)
        assert (str(exc.value) ==
                'there is already a lazy property for "foo" on the application scope')


class TestTransportContext:
    def test_getattr_parent_attr(self, app_context, transport_context):
        app_context.a = 6
        assert transport_context.a == 6

    def test_lazy_property(self, transport_context):
        transport_context.add_lazy_property(ContextScope.transport, 'a', lambda ctx: ctx)
        assert transport_context.a is transport_context

    def test_getattr_error(self, transport_context):
        exc = pytest.raises(AttributeError, getattr, transport_context, 'nonexistent')
        assert str(exc.value) == 'no such context property: nonexistent'


class TestHandlerContext:
    def test_getattr_parent_attr(self, transport_context, handler_context):
        transport_context.a = 6
        assert handler_context.a == 6

    def test_lazy_property(self, handler_context):
        handler_context.add_lazy_property(ContextScope.handler, 'a', lambda ctx: ctx)
        assert handler_context.a is handler_context

    def test_getattr_error(self, handler_context):
        exc = pytest.raises(AttributeError, getattr, handler_context, 'nonexistent')
        assert str(exc.value) == 'no such context property: nonexistent'
