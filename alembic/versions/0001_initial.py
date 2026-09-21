"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-14

NOTE: finalized before any production deployment (no production database
has ever applied this revision). Primary keys mirror qrgen.db.ID_TYPE:
BigInteger with an Integer variant on SQLite, where only INTEGER primary
keys alias the rowid and auto-assign without an explicit id.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Same type as the ORM models: BIGINT on PostgreSQL, plain INTEGER on
# SQLite (where BIGINT keys cannot alias the rowid and would reject
# inserts without an explicit id).
ID_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", ID_TYPE, primary_key=True, autoincrement=True),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("accept_language", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=4), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("ip_address", "user_agent", name="uq_clients_ip_ua"),
    )
    op.create_table(
        "rate_limit_windows",
        sa.Column("id", ID_TYPE, primary_key=True, autoincrement=True),
        sa.Column("client_id", ID_TYPE, nullable=False),
        sa.Column("endpoint", sa.String(length=16), nullable=False),
        sa.Column("window_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "client_id", "endpoint", "window_date", name="uq_rate_window"
        ),
    )
    op.create_table(
        "qr_inputs",
        sa.Column("id", ID_TYPE, primary_key=True, autoincrement=True),
        sa.Column("client_id", ID_TYPE, nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_rate_limit_windows_window_date", "rate_limit_windows", ["window_date"]
    )
    op.create_index("ix_qr_inputs_created_at", "qr_inputs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_qr_inputs_created_at", table_name="qr_inputs")
    op.drop_index("ix_rate_limit_windows_window_date", table_name="rate_limit_windows")
    op.drop_table("qr_inputs")
    op.drop_table("rate_limit_windows")
    op.drop_table("clients")
