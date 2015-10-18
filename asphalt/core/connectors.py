from typing import Tuple, Optional, Dict, Any, Union
from asyncio import StreamReader, StreamWriter
from ipaddress import IPv4Address, IPv6Address
from abc import ABCMeta, abstractmethod
from ssl import SSLContext
import asyncio
import logging

from .context import ResourceNotFound, Context
from .util import asynchronous, PluginContainer

__all__ = 'ConnectorError', 'Connector', 'TCPConnector', 'UnixSocketConnector', 'create_connector'

logger = logging.getLogger(__name__)


class ConnectorError(LookupError):
    """
    Raised by :func:`create_connector` when the requested connector could not be created or
    looked up.

    :ivar endpoint: the endpoint value
    """

    def __init__(self, endpoint, message):
        super().__init__(endpoint, message)
        self.endpoint = endpoint
        self.message = message

    def __str__(self):
        return self.message


class Connector(metaclass=ABCMeta):
    """
    The base class for all connectors. Each connector class contains logic for making a connection
    to some endpoint, usually over the network or inter-process communication channels.
    """

    __slots__ = ()

    @abstractmethod
    def connect(self) -> Tuple[StreamReader, StreamWriter]:
        """
        Makes a connection to the connector's endpoint. This is a coroutine.

        :raises ConnectionError: if connection could not be established
        """

    @classmethod
    @abstractmethod
    def parse(cls, endpoint, defaults: Dict[str, Any]) -> Optional['Connector']:
        """
        Returns an instance of this connector class if the type and value of ``endpoint`` are
        compatible with this connector type.

        Subclasses should indicate what keys in ``defaults`` they can use.

        :param endpoint: connector specific
        :param defaults: a dictionary of default values, some specific to certain connector types
        :return: an instance of this class or ``None`` if ``endpoint`` was not compatible with this
                 connector
        """

    @abstractmethod
    def __str__(self):  # pragma: no cover
        pass


class TCPConnector(Connector):
    """
    Connects to the specified host/port over TCP/IP, optionally using TLS.

    The port can be omitted if one has been specified in the defaults.

    The endpoint value can be one of the following:
      * a string prefixed with ``tcp://`` or ``tcp+ssl://``
      * an :class:`~ipaddress.IPv4Address` or :class:`~ipaddress.IPv6Address`
      * a dictionary containing the following keys:

        * ``host``: string, :class:`~ipaddress.IPv4Address` or :class:`~ipaddress.IPv6Address`
        * ``port`` (optional): an integer between 1 and 65535
        * ``ssl`` (optional): either a boolean or an :class:`~ssl.SSLContext`
        * ``timeout`` (optional): the number of seconds to wait for a connection to be established\
          before raising :class:`~asyncio.TimeoutError`

    TLS is enabled for this connection if any of these conditions are met:
      #. The endpoint is a string that starts with ``tcp+ssl://``
      #. The endpoint is a dict where ``ssl`` is set to a truthy value
      #. ``ssl`` is ``True`` in ``defaults`` and ssl is not explicitly disabled for this connection

    For raw IPv6 address:port combinations, use the standard [...]:port format.
    For example, ``[::1]:1234`` connects to localhost on port 1234 with IPv6.
    """

    __slots__ = 'host', 'port', 'ssl', 'timeout'

    def __init__(self, host: str, port: int, ssl: Union[bool, SSLContext]=False,
                 timeout: Union[int, float]=30):
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise ValueError('port must be an integer between 1 and 65535, not {}'.format(port))

        self.host = host
        self.port = port
        self.ssl = ssl
        self.timeout = timeout

    @classmethod
    def parse(cls, endpoint, defaults: Dict[str, Any]):
        if isinstance(endpoint, str):
            new_endpoint = {}
            if endpoint.startswith('tcp+ssl://'):
                endpoint = endpoint[10:]
                new_endpoint['ssl'] = True
            elif endpoint.startswith('tcp://'):
                endpoint = endpoint[6:]
            elif '://' in endpoint:
                return

            if endpoint.startswith('[') and ']' in endpoint:
                # IPv6 address
                new_endpoint['host'], rest = endpoint[1:].split(']', 1)
                if rest.startswith(':'):
                    new_endpoint['port'] = int(rest[1:])
            elif endpoint[0].isalnum():
                # Host name or IPv4 address
                if ':' in endpoint:
                    new_endpoint['host'], port = endpoint.split(':', 1)
                    new_endpoint['port'] = int(port)
                else:
                    new_endpoint['host'] = endpoint
            else:
                raise ValueError('invalid address: {}'.format(endpoint))

            endpoint = new_endpoint
        elif isinstance(endpoint, (IPv4Address, IPv6Address)):
            endpoint = {'host': endpoint}

        if isinstance(endpoint, dict) and 'host' in endpoint:
            host = str(endpoint['host'])
            port = endpoint.get('port', defaults.get('port'))
            ssl = endpoint.get('ssl', defaults.get('ssl', False))
            ssl = defaults.get('ssl_context', True) if ssl else False
            timeout = endpoint.get('timeout', defaults.get('timeout', 30))
            if port is None:
                raise ValueError('no port has been specified')

            return cls(host, port, ssl, timeout)

    @asynchronous
    def connect(self):
        try:
            coro = asyncio.open_connection(self.host, self.port, ssl=self.ssl)
            return (yield from asyncio.wait_for(coro, self.timeout))
        except Exception as e:
            logger.error('Error connecting to %s:%d: %s', self.host, self.port, e)
            raise ConnectionError('Error connecting to {}: {}'.format(self, e)) from e

    def __str__(self):
        prefix = 'tcp+ssl://' if self.ssl else 'tcp://'
        return '{prefix}{self.host}:{self.port}'.format(prefix=prefix, self=self)


class UnixSocketConnector(Connector):
    """
    A connector that connects to a UNIX domain socket, optionally using TLS.

    The endpoint value can be one of the following:
      * a string starting with either ``unix://`` or ``unix+ssl://`` followed by the socket
        pathname (absolute or relative to current working directory)
      * a dictionary containing the following keys:

        * ``path``: a string or :class:`~pathlib.Path`, specifying the target socket
        * ``ssl``: either a boolean or an :class:`~ssl.SSLContext`

    TLS is enabled for this connection if any of these conditions are met:
      #. The endpoint is a string that starts with ``unix+ssl://``
      #. The endpoint is a dict where ``ssl`` is set to a truthy value
      #. ``ssl`` is ``True`` in ``defaults`` and ssl is not explicitly disabled for this connection
    """

    __slots__ = 'path', 'ssl', 'timeout'

    def __init__(self, path: str, ssl: Union[bool, SSLContext]=False,
                 timeout: Union[int, float]=30):
        self.path = path
        self.ssl = ssl
        self.timeout = timeout

    @classmethod
    def parse(cls, endpoint, defaults: Dict[str, Any]):
        if isinstance(endpoint, str):
            if endpoint.startswith('unix://'):
                endpoint = {'path': endpoint[7:], 'ssl': False}
            elif endpoint.startswith('unix+ssl://'):
                endpoint = {'path': endpoint[11:], 'ssl': True}
            else:
                return

        if isinstance(endpoint, dict) and 'path' in endpoint:
            path = str(endpoint['path'])
            ssl = endpoint.get('ssl', defaults.get('ssl', False))
            ssl = defaults.get('ssl_context', True) if ssl else False
            timeout = endpoint.get('timeout', defaults.get('timeout', 30))
            return cls(path, ssl, timeout)

    @asynchronous
    def connect(self):
        try:
            coro = asyncio.open_unix_connection(self.path, ssl=self.ssl)
            return (yield from asyncio.wait_for(coro, self.timeout))
        except Exception as e:
            logger.error('Error connecting to %s: %s', self.path, e)
            raise ConnectionError('Error connecting to {}: {}'.format(self, e)) from e

    def __str__(self):
        prefix = 'unix+ssl://' if self.ssl else 'unix://'
        return prefix + self.path


@asynchronous
def create_connector(endpoint, defaults: Dict[str, Any]=None, ctx: Context=None, *,
                     timeout: int=None) -> Connector:
    """
    Creates or looks up a connector from the given endpoint.
    The endpoint is offered to each connector class in turn until one of them produces a connector
    instance, which is then returned.

    It is also possible to resolve named connectors by providing a context as the ``ctx`` argument.
    Then supply an ``endpoint`` in the ``resource://<name>`` format (e.g. ``resource://foo`` to
    get the connector resource named "foo").

    :param endpoint: a connector specific value representing the endpoint of the connection
    :param defaults: a dictionary of default values (see the documentation of each connector
                     type)
    :param ctx: a context for resolving connector names
    :param timeout: timeout for resource lookup (if endpoint refers to a resource; omit to use the
                    default timeout)
    :raises ConnectorError: if all the connector types reject this endpoint or the named connector
                            resource could not be found
    """

    if isinstance(endpoint, str) and endpoint.startswith('resource://'):
        if ctx is None:
            raise ConnectorError(endpoint, 'named connector resource requested but no context '
                                           'was provided for resource loading')

        alias = endpoint[11:]
        try:
            return (yield from ctx.request_resource(Connector, alias, timeout=timeout))
        except ResourceNotFound as e:
            raise ConnectorError(
                endpoint, 'connector resource "{}" could not be found'.format(alias)) from e

    for connector_class in connectors.all():
        connector = connector_class.parse(endpoint, defaults or {})
        if connector:
            return connector

    raise ConnectorError(endpoint, '{!r} is not a valid connector endpoint'.format(endpoint))

connectors = PluginContainer('asphalt.core.connectors', Connector)
