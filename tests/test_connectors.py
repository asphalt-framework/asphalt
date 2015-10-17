from asyncio import Future
from ipaddress import IPv4Address
from unittest.mock import patch
import os

import pytest

from asphalt.core.connectors import (Connector, TCPConnector, UnixSocketConnector,
                                     create_connector, ConnectorError)
from asphalt.core.context import Context


class TestTCPConnector:
    @pytest.mark.parametrize('endpoint, defaults, expected', [
        ('tcp://1.2.3.4:5000', {}, {'host': '1.2.3.4', 'port': 5000, 'ssl': False}),
        ('tcp+ssl://1.2.3.4:5000', {}, {'host': '1.2.3.4', 'port': 5000, 'ssl': True}),
        ('tcp://1.2.3.4', {'port': 4000}, {'host': '1.2.3.4', 'port': 4000, 'ssl': False}),
        (IPv4Address('1.2.3.4'), {'port': 4000}, {'host': '1.2.3.4', 'port': 4000, 'ssl': False}),
        ('1.2.3.4:5000', {'port': 4000}, {'host': '1.2.3.4', 'port': 5000, 'ssl': False}),
        ('1.2.3.4', {'port': 4000}, {'host': '1.2.3.4', 'port': 4000, 'ssl': False}),
        ('[fe80::1%1]:5000', {'port': 4000}, {'host': 'fe80::1%1', 'port': 5000, 'ssl': False}),
        ('[fe80::1%1]', {'port': 4000}, {'host': 'fe80::1%1', 'port': 4000, 'ssl': False}),
        ({'host': 'a.b', 'port': 9000, 'timeout': 15}, {'timeout': 20},
         {'host': 'a.b', 'port': 9000, 'timeout': 15, 'ssl': False})
    ])
    def test_parse(self, endpoint, defaults, expected):
        connector = TCPConnector.parse(endpoint, defaults)
        for attr, value in expected.items():
            assert getattr(connector, attr) == value

    def test_parse_no_port(self):
        exc = pytest.raises(ValueError, TCPConnector.parse, '1.2.3.4', {})
        assert str(exc.value) == 'no port has been specified'

    def test_parse_invalid_address(self):
        exc = pytest.raises(ValueError, TCPConnector.parse, '.1.2.3.4', {})
        assert str(exc.value) == 'invalid address: .1.2.3.4'

    def test_invalid_port(self):
        exc = pytest.raises(ValueError, TCPConnector, '1.2.3.4', -1)
        assert str(exc.value) == 'port must be an integer between 1 and 65535, not -1'

    @pytest.mark.asyncio
    def test_connect(self):
        connector = TCPConnector('1.2.3.4', 5000)
        future = Future()
        future.set_result((1, 2))
        with patch('asyncio.open_connection', return_value=future) as open_connection:
            reader, writer = yield from connector.connect()

        open_connection.assert_called_once_with('1.2.3.4', 5000, ssl=False)
        assert (reader, writer) == (1, 2)

    @pytest.mark.asyncio
    def test_connect_error(self):
        connector = TCPConnector('1.2.3.4', 5000)
        connection_patch = patch('asyncio.open_connection', side_effect=Exception('foo'))
        with connection_patch, pytest.raises(ConnectionError) as exc:
            yield from connector.connect()

        assert str(exc.value) == 'Error connecting to tcp://1.2.3.4:5000: foo'

    @pytest.mark.parametrize('connector, expected', [
        (TCPConnector('1.2.3.4', 400, False), 'tcp://1.2.3.4:400'),
        (TCPConnector('1.2.3.4', 400, True), 'tcp+ssl://1.2.3.4:400')
    ])
    def test_str(self, connector, expected):
        assert str(connector) == expected


@pytest.mark.skipif(os.name != 'posix', reason='os.name != "posix"')
class TestUnixSocketConnector:
    @pytest.mark.parametrize('endpoint, defaults, expected', [
        ('unix:///some/path', {}, {'path': '/some/path', 'ssl': False}),
        ('unix+ssl:///some/path', {}, {'path': '/some/path', 'ssl': True}),
        ({'path': '@somename', 'timeout': 15}, {'timeout': 20},
         {'path': '@somename', 'timeout': 15, 'ssl': False})
    ])
    def test_parse(self, endpoint, defaults, expected):
        connector = UnixSocketConnector.parse(endpoint, defaults)
        for attr, value in expected.items():
            assert getattr(connector, attr) == value

    @pytest.mark.asyncio
    def test_connect(self):
        connector = UnixSocketConnector('/some/path')
        future = Future()
        future.set_result((1, 2))
        with patch('asyncio.open_unix_connection', return_value=future) as open_unix_connection:
            reader, writer = yield from connector.connect()

        open_unix_connection.assert_called_once_with('/some/path', ssl=False)
        assert (reader, writer) == (1, 2)

    @pytest.mark.asyncio
    def test_connect_error(self):
        connector = UnixSocketConnector('/some/path')
        connection_patch = patch('asyncio.open_unix_connection', side_effect=Exception('foo'))
        with connection_patch, pytest.raises(ConnectionError) as exc:
            yield from connector.connect()

        assert str(exc.value) == 'Error connecting to unix:///some/path: foo'

    @pytest.mark.parametrize('connector, expected', [
        (UnixSocketConnector('/some/path', False), 'unix:///some/path'),
        (UnixSocketConnector('/some/path', True), 'unix+ssl:///some/path')
    ])
    def test_str(self, connector, expected):
        assert str(connector) == expected


class TestCreateConnector:
    @pytest.mark.asyncio
    def test_create_connector(self):
        """Tests that creating a connector with a URL style endpoint works."""

        result = yield from create_connector('tcp://127.0.0.1:5000')
        assert isinstance(result, TCPConnector)
        assert result.host == '127.0.0.1'
        assert result.port == 5000

    @pytest.mark.asyncio
    def test_resource(self):
        """Tests that resource:// connectors work."""

        connector = TCPConnector('127.0.0.1', 5000)
        ctx = Context()
        yield from ctx.publish_resource(connector, 'foo', types=[Connector])
        result = yield from create_connector('resource://foo', ctx=ctx, timeout=0)
        assert result is connector

    @pytest.mark.asyncio
    def test_resource_not_found(self):
        """Tests that when a connector resource is not found, ConnectorError is raised."""

        with pytest.raises(ConnectorError) as exc:
            yield from create_connector('resource://foo', ctx=Context(), timeout=0)

        assert str(exc.value) == 'connector resource "foo" could not be found'

    @pytest.mark.asyncio
    def test_resource_no_context(self):
        """
        Tests that attempting to look up a connector resource without a context raises the
        appropriate exception.
        """

        with pytest.raises(ConnectorError) as exc:
            yield from create_connector('resource://foo')

        assert str(exc.value) == ('named connector resource requested but no context was provided '
                                  'for resource loading')

    @pytest.mark.asyncio
    def test_invalid_endpoint(self):
        """Tests that specifying an invalid endpoint raises the appropriate exception."""

        with pytest.raises(ConnectorError) as exc:
            yield from create_connector('blah://foo')

        assert str(exc.value) == "'blah://foo' is not a valid connector endpoint"
