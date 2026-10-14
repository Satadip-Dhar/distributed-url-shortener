import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_read_db, get_redis, get_write_db
from app.config import settings
from app.metrics import REDIRECT_LATENCY, REDIRECTS_TOTAL, URLS_CREATED
from app.models import URL, ClickEvent
from app.schemas import ShortenRequest, ShortenResponse, StatsResponse
from app.services.analytics import record_click
from app.services.shortener import create_short_url

router = APIRouter()


@router.post("/api/v1/shorten", response_model=ShortenResponse, status_code=201)
async def shorten_url(
    body: ShortenRequest,
    db: AsyncSession = Depends(get_write_db),
    redis: Redis = Depends(get_redis),
):
    """Create a shortened URL."""
    url = await create_short_url(db, str(body.url), body.redirect_type)
    URLS_CREATED.inc()

    # Warm the cache immediately (value + redirect type)
    await redis.setex(
        f"url:{url.short_code}",
        settings.CACHE_TTL_SECONDS,
        url.original_url,
    )
    await redis.setex(
        f"url:{url.short_code}:type",
        settings.CACHE_TTL_SECONDS,
        str(url.redirect_type),
    )

    return ShortenResponse(
        short_url=f"{settings.BASE_URL}/{url.short_code}",
        short_code=url.short_code,
        original_url=url.original_url,
    )


@router.get("/{short_code}")
async def redirect(
    short_code: str,
    request: Request,
    db: AsyncSession = Depends(get_read_db),          # reads from replica
    write_db: AsyncSession = Depends(get_write_db),   # analytics to primary
    redis: Redis = Depends(get_redis),
):
    """
    Redirect to original URL.
    Cache-aside pattern:
      1. Check Redis -> if hit, redirect immediately
      2. If miss -> query PostgreSQL replica -> populate cache -> redirect
      3. Record click event asynchronously to primary
    """
    started_at = time.perf_counter()

    # Step 1: Cache lookup
    cached_url = await redis.get(f"url:{short_code}")
    cache_hit = cached_url is not None

    if cached_url:
        original_url = cached_url
        redirect_type = int(
            await redis.get(f"url:{short_code}:type")
            or settings.DEFAULT_REDIRECT_STATUS
        )
    else:
        # Step 2: DB lookup (read replica)
        result = await db.execute(
            select(URL).where(URL.short_code == short_code, URL.is_active.is_(True))
        )
        url = result.scalar_one_or_none()
        if not url:
            raise HTTPException(status_code=404, detail="Short URL not found")

        original_url = url.original_url
        redirect_type = url.redirect_type

        # Populate cache
        pipe = redis.pipeline()
        pipe.setex(f"url:{short_code}", settings.CACHE_TTL_SECONDS, original_url)
        pipe.setex(
            f"url:{short_code}:type",
            settings.CACHE_TTL_SECONDS,
            str(redirect_type),
        )
        await pipe.execute()

    # Step 3: Record click (fire-and-forget to primary)
    await record_click(write_db, short_code, request)

    REDIRECTS_TOTAL.labels(
        status_code=str(redirect_type),
        cache_hit=str(cache_hit).lower(),
    ).inc()
    REDIRECT_LATENCY.observe(time.perf_counter() - started_at)

    return RedirectResponse(url=original_url, status_code=redirect_type)


@router.get("/api/v1/stats/{short_code}", response_model=StatsResponse)
async def get_stats(
    short_code: str,
    db: AsyncSession = Depends(get_read_db),
):
    """Return click analytics for a short URL."""
    result = await db.execute(select(URL).where(URL.short_code == short_code))
    url = result.scalar_one_or_none()
    if not url:
        raise HTTPException(status_code=404)

    clicks_result = await db.execute(
        select(ClickEvent)
        .where(ClickEvent.url_id == url.id)
        .order_by(ClickEvent.clicked_at.desc())
        .limit(100)
    )

    return StatsResponse(
        short_code=url.short_code,
        original_url=url.original_url,
        total_clicks=url.click_count,
        recent_clicks=clicks_result.scalars().all(),
        created_at=url.created_at,
    )
