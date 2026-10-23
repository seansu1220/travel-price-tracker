"""P0 合規基礎：來源登錄表與稽核日誌

Revision ID: 0001
Revises:
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("tier", sa.String(1), nullable=False),
        sa.Column("terms_url", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.Date(), nullable=False),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("allowed_hosts", sa.JSON(), nullable=False),
        sa.Column("allowed_paths", sa.JSON(), nullable=False),
        sa.Column("min_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("daily_call_limit", sa.Integer(), nullable=False),
        sa.Column("cost_per_call_minor", sa.Integer(), nullable=False),
        sa.Column("monthly_budget_minor", sa.Integer(), nullable=False),
        sa.Column("budget_currency", sa.String(3), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("tier IN ('A', 'B', 'C')", name="ck_sources_tier"),
        sa.CheckConstraint("kind IN ('flight', 'hotel')", name="ck_sources_kind"),
    )
    op.create_table(
        "fetch_logs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(64), nullable=False),
        sa.Column("robots_verdict", sa.String(32), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cost_minor", sa.Integer(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fetch_logs_created_at", "fetch_logs", ["created_at"])
    op.create_index("ix_fetch_logs_source_created", "fetch_logs", ["source_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_fetch_logs_source_created", table_name="fetch_logs")
    op.drop_index("ix_fetch_logs_created_at", table_name="fetch_logs")
    op.drop_table("fetch_logs")
    op.drop_table("sources")
