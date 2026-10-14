from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import URL, ClickEvent

USER_AGENT_MAX_LENGTH = 512
REFERRER_MAX_LENGTH = 2048


def _truncate(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit]


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP, honouring X-Forwarded-For from the reverse proxy."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else None


async def record_click(db: AsyncSession, short_code: str, request: Request) -> None:
    """Record one click event and bump the URL's click counter on the primary."""
    result = await db.execute(select(URL).where(URL.short_code == short_code))
    url = result.scalar_one_or_none()
    if url is None:
        return

    click = ClickEvent(
        url_id=url.id,
        ip_address=_client_ip(request),
        user_agent=_truncate(request.headers.get("user-agent"), USER_AGENT_MAX_LENGTH),
        referrer=_truncate(request.headers.get("referer"), REFERRER_MAX_LENGTH),
    )
    db.add(click)

    # Atomic increment: safe when many redirects land at the same time.
    await db.execute(
        update(URL).where(URL.id == url.id).values(click_count=URL.click_count + 1)
    )
    await db.commit()
