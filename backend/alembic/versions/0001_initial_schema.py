"""create urls and click_events tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "urls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("short_code", sa.String(length=10), nullable=False),
        sa.Column("original_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redirect_type", sa.Integer(), nullable=True),
        sa.Column("click_count", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_urls_short_code"), "urls", ["short_code"], unique=True)
    op.create_index("idx_short_code_active", "urls", ["short_code", "is_active"], unique=False)

    op.create_table(
        "click_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("url_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "clicked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("referrer", sa.String(length=2048), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.ForeignKeyConstraint(["url_id"], ["urls.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_click_events_url_id"), "click_events", ["url_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_click_events_url_id"), table_name="click_events")
    op.drop_table("click_events")
    op.drop_index("idx_short_code_active", table_name="urls")
    op.drop_index(op.f("ix_urls_short_code"), table_name="urls")
    op.drop_table("urls")
