import pytest_asyncio

from tests.unit.api.support import build


@pytest_asyncio.fixture
async def server():
    s = build()
    await s.start()
    yield s
    await s.stop()


@pytest_asyncio.fixture
async def session():
    import aiohttp

    async with aiohttp.ClientSession() as client:
        yield client
