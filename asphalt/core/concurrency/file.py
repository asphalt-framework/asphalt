from concurrent.futures import Executor
from pathlib import Path
from typing import Union, Optional

from typeguard import check_argument_types

from asphalt.core.concurrency import threadpool, async_generator, yield_async, call_in_thread

__all__ = ('AsyncFileWrapper', 'open_async')


class AsyncFileWrapper:
    """
    Wraps certain file I/O operations so they're guaranteed to run in a thread pool.

    The wrapped methods work like coroutines when called in the event loop thread, but when called
    in any other thread, they work just like the methods of the ``file`` type.

    This class supports use as an asynchronous context manager.

    The wrapped methods are:

    * ``flush()``
    * ``read()``
    * ``readline()``
    * ``readlines()``
    * ``seek()``
    * ``truncate()``
    * ``write()``
    * ``writelines()``
    """

    __slots__ = ('_open_args', '_open_kwargs', '_executor', '_raw_file', 'flush', 'read',
                 'readline', 'readlines', 'seek', 'truncate', 'write', 'writelines')

    def __init__(self, path: str, args: tuple, kwargs: dict, executor: Optional[Executor]):
        self._open_args = (path,) + args
        self._open_kwargs = kwargs
        self._executor = executor
        self._raw_file = None

    def __getattr__(self, name):
        return getattr(self._raw_file, name)

    def __await__(self):
        if self._raw_file is None:
            self._raw_file = yield from call_in_thread(
                open, *self._open_args, executor=self._executor, **self._open_kwargs)
            self.flush = threadpool(self._executor)(self._raw_file.flush)
            self.read = threadpool(self._executor)(self._raw_file.read)
            self.readline = threadpool(self._executor)(self._raw_file.readline)
            self.readlines = threadpool(self._executor)(self._raw_file.readlines)
            self.seek = threadpool(self._executor)(self._raw_file.seek)
            self.truncate = threadpool(self._executor)(self._raw_file.truncate)
            self.write = threadpool(self._executor)(self._raw_file.write)
            self.writelines = threadpool(self._executor)(self._raw_file.writelines)

        return self

    def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._raw_file.close()

    @async_generator
    async def async_readchunks(self, size: int):
        """
        Read data from the file in chunks.

        :param size: the maximum number of bytes or characters to read at once
        :return: an asynchronous iterator yielding bytes or strings

        """
        assert check_argument_types()
        while True:
            data = await self.read(size)
            if data:
                await yield_async(data)
            else:
                return


def open_async(file: Union[str, Path], *args, executor: Executor = None,
               **kwargs) -> AsyncFileWrapper:
    """
    Open a file and wrap it in an :class:`~AsyncFileWrapper`.

    :param file: the file path to open
    :param args: positional arguments to :func:`open`
    :param executor: the ``executor`` argument to :class:`~AsyncFileWrapper`
    :param kwargs: keyword arguments to :func:`open`
    :return: the wrapped file object

    """
    assert check_argument_types()
    return AsyncFileWrapper(str(file), args, kwargs, executor)
