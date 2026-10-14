import pytest
from app.cache import cache
from app.database import WriteSession
from app.models import URL
from sqlalchemy import update


async def _create(client, url="https://example.com", redirect_type=302):
    res = await client.post(
        "/api/v1/shorten", json={"url": url, "redirect_type": redirect_type}
    )
    assert res.status_code == 201
    return res.json()["short_code"]


@pytest.mark.asyncio
async def test_redirect_302_by_default(client):
    code = await _create(client)
    res = await client.get(f"/{code}", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "https://example.com"


@pytest.mark.asyncio
async def test_redirect_301_when_requested(client):
    code = await _create(client, redirect_type=301)
    res = await client.get(f"/{code}", follow_redirects=False)
    assert res.status_code == 301
    assert res.headers["location"] == "https://example.com"


@pytest.mark.asyncio
async def test_cache_warmed_on_shorten(client):
    code = await _create(client)
    redis_client = await cache.get_client()
    assert await redis_client.get(f"url:{code}") == "https://example.com"
    assert await redis_client.get(f"url:{code}:type") == "302"


@pytest.mark.asyncio
async def test_cache_populated_after_cache_miss(client):
    code = await _create(client)
    redis_client = await cache.get_client()
    await redis_client.delete(f"url:{code}", f"url:{code}:type")

    res = await client.get(f"/{code}", follow_redirects=False)
    assert res.status_code == 302
    assert await redis_client.get(f"url:{code}") == "https://example.com"
    assert await redis_client.get(f"url:{code}:type") == "302"


@pytest.mark.asyncio
async def test_unknown_code_returns_404(client):
    res = await client.get("/doesnotexist", follow_redirects=False)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_inactive_url_returns_404(client):
    code = await _create(client)

    redis_client = await cache.get_client()
    await redis_client.delete(f"url:{code}", f"url:{code}:type")

    async with WriteSession() as session:
        await session.execute(
            update(URL).where(URL.short_code == code).values(is_active=False)
        )
        await session.commit()

    res = await client.get(f"/{code}", follow_redirects=False)
    assert res.status_code == 404
