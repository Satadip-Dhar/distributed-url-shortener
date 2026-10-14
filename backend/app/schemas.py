from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ShortenRequest(BaseModel):
    url: str = Field(..., max_length=2048, description="The long URL to shorten")
    redirect_type: Literal[301, 302] = 302

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("url must be a valid http:// or https:// URL")
        return value


class ShortenResponse(BaseModel):
    short_url: str
    short_code: str
    original_url: str


class ClickEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clicked_at: datetime
    ip_address: str | None = None
    user_agent: str | None = None
    referrer: str | None = None
    country: str | None = None


class StatsResponse(BaseModel):
    short_code: str
    original_url: str
    total_clicks: int
    recent_clicks: list[ClickEventOut]
    created_at: datetime
