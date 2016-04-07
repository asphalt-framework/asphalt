from contextlib import closing
from pathlib import Path

import pytest

from asphalt.core.concurrency.file import open_async


@pytest.fixture(scope='module')
def testdata():
    return b''.join(bytes([i] * 1000) for i in range(10))


@pytest.fixture
def testdatafile(tmpdir_factory, testdata):
    file = tmpdir_factory.mktemp('file').join('testdata')
    file.write(testdata)
    return Path(str(file))


@pytest.mark.asyncio
async def test_read(testdatafile, testdata):
    async with open_async(testdatafile, 'rb') as f:
        data = await f.read()

    assert f.closed
    assert data == testdata


@pytest.mark.asyncio
async def test_write(testdatafile, testdata):
    async with open_async(testdatafile, 'ab') as f:
        await f.write(b'f' * 1000)

    assert testdatafile.stat().st_size == len(testdata) + 1000


@pytest.mark.asyncio
async def test_async_readchunks(testdatafile):
    value = 0
    async with open_async(testdatafile, 'rb') as f:
        async for chunk in f.async_readchunks(1000):
            assert chunk == bytes([value] * 1000)
            value += 1


@pytest.mark.asyncio
async def test_no_contextmanager(testdatafile, testdata):
    """Test that open_async() can be used without an async context manager."""
    with closing(await open_async(testdatafile, 'rb')) as f:
        data = await f.read()

    assert f.closed
    assert data == testdata
