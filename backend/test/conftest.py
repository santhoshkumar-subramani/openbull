import pytest

from backend.utils.redis_client import close_redis


@pytest.fixture(autouse=True)
async def _reset_redis_client() -> None:
    """Ensure each async test gets a fresh Redis client bound to its loop."""
    await close_redis()
    yield
    await close_redis()
