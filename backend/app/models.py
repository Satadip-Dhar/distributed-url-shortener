from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.sql import func

from app.database import Base


class URL(Base):
    __tablename__ = "urls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    short_code = Column(String(10), unique=True, nullable=False, index=True)
    original_url = Column(String(2048), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    redirect_type = Column(Integer, default=302)  # 301 or 302
    click_count = Column(BigInteger, default=0)
    is_active = Column(Boolean, default=True)

    # Composite index for quick lookups
    __table_args__ = (
        Index("idx_short_code_active", "short_code", "is_active"),
    )


class ClickEvent(Base):
    __tablename__ = "click_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_id = Column(BigInteger, ForeignKey("urls.id"), nullable=False, index=True)
    clicked_at = Column(DateTime(timezone=True), server_default=func.now())
    ip_address = Column(String(45))  # IPv6 max length
    user_agent = Column(String(512))
    referrer = Column(String(2048))
    country = Column(String(2))  # GeoIP later (optional)
