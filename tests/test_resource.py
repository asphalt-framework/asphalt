from functools import partial

import pytest

from asphalt.core.resource import (
    Resource, ResourceEventType, ResourceCollection, ResourceConflict, ResourceNotFoundError,
    ResourceEventListener)


class TestResource:
    @pytest.fixture
    def resource(self):
        return Resource(6, ('int', 'object'), 'foo', 'bar.foo')

    def test_repr(self, resource: Resource):
        assert repr(resource) == ("Resource(types=('int', 'object'), alias='foo', "
                                  "value=6, context_var='bar.foo')")

    def test_str(self, resource: Resource):
        assert str(resource) == ("types=('int', 'object'), alias='foo', "
                                 "value=6, context_var='bar.foo'")


class TestResourceEventListener:
    @pytest.mark.parametrize('contextmanager', [False, True], ids=['unlisten', 'contextmanager'])
    def test_unlisten(self, contextmanager):
        callbacks = []
        listener = ResourceEventListener(callbacks, lambda evt: None)
        callbacks.append(listener)
        if contextmanager:
            with listener:
                pass
        else:
            listener.unlisten()

        assert listener not in callbacks


class TestResourceManager:
    @pytest.fixture
    def resources(self):
        return ResourceCollection()

    @pytest.mark.asyncio
    @pytest.mark.parametrize('delay', [False, True], ids=['immediate', 'delayed'])
    def test_add(self, resources: ResourceCollection, event_loop, delay):
        """Tests that a resource is properly added to the collection and listeners are notified."""

        events = []
        resources.add_listener(ResourceEventType.added, events.append)
        if delay:
            call = partial(resources.add, 6, 'foo', 'foo.bar', extra_types=float)
            event_loop.call_soon(call)
        else:
            resources.add(6, 'foo', 'foo.bar', extra_types=float)

        value = yield from resources.request(int, 'foo', timeout=2)
        assert value == 6
        assert len(events) == 1
        resource = events[0].resource
        assert resource.alias == 'foo'
        assert resource.context_var == 'foo.bar'
        assert resource.value == 6
        assert resource.types == ('int', 'float')

    def test_add_name_conflict(self, resources: ResourceCollection):
        """Tests that add() won't let replace existing resources."""

        resource = resources.add(5, 'foo')
        exc = pytest.raises(ResourceConflict, resources.add, 4, 'foo')
        assert exc.value.resource is resource
        assert str(exc.value) == ('"foo" conflicts with Resource(types=(\'int\',), alias=\'foo\', '
                                  'value=5, context_var=None)')

    @pytest.mark.asyncio
    def test_add_event_conflict(self, resources: ResourceCollection):
        """
        Tests that a resource adding is cancelled if an event listener raises ResourceConflict.
        """

        def listener(event):
            raise ResourceConflict('conflict test', event.resource)

        resources.add_listener(ResourceEventType.added, listener)
        pytest.raises(ResourceConflict, resources.add, 5)
        with pytest.raises(ResourceNotFoundError):
            yield from resources.request(int, timeout=0)

    @pytest.mark.asyncio
    def test_remove(self, resources: ResourceCollection):
        """Tests that resources can be removed and that the listeners are notified."""

        resource = resources.add(4)

        events = []
        resources.add_listener(ResourceEventType.removed, events.append)
        resources.remove(resource)

        assert len(events) == 1
        assert events[0].resource.value == 4

        with pytest.raises(ResourceNotFoundError):
            yield from resources.request(int, timeout=0)

    def test_remove_nonexistent(self, resources: ResourceCollection):
        resource = Resource(5, ('int',), 'default', None)
        exc = pytest.raises(LookupError, resources.remove, resource)
        assert str(exc.value) == ("Resource(types=('int',), alias='default', value=5, "
                                  "context_var=None) not found in this collection")

    @pytest.mark.asyncio
    def test_request_timeout(self, resources: ResourceCollection):
        with pytest.raises(ResourceNotFoundError) as exc:
            yield from resources.request(int, timeout=0.2)

        assert str(exc.value) == "no matching resource was found for type='int' alias='default'"

    @pytest.mark.asyncio
    @pytest.mark.parametrize('bad_arg, errormsg', [
        ('type', 'type must be a type or a nonempty string'),
        ('alias', 'alias must be a nonempty string')
    ], ids=['bad_type', 'bad_alias'])
    def test_bad_request(self, resources: ResourceCollection, bad_arg, errormsg):
        type_ = None if bad_arg == 'type' else 'foo'
        alias = None if bad_arg == 'alias' else 'foo'
        with pytest.raises(ValueError) as exc:
            yield from resources.request(type_, alias)
        assert str(exc.value) == errormsg
