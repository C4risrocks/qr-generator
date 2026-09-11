"""add created_at to rate_limit_windows

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-10

"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _sqlite_table() -> sa.Table:
    """Explicit target schema for the SQLite rebuild (see 0001 for the
    origin): plain INTEGER keys, both named indexes, named unique
    constraint and a NOT NULL created_at with a constant default."""
    return sa.Table(
        "rate_limit_windows",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.String(length=16), nullable=False),
        sa.Column("window_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "client_id", "endpoint", "window_date", name="uq_rate_window"
        ),
        sa.Index("ix_rate_limit_windows_window_date", "window_date"),
    )


def upgrade() -> None:
    # SQLite rejects ADD COLUMN with a non-constant default (such as
    # CURRENT_TIMESTAMP) on tables that already hold rows, so the column is
    # added nullable, backfilled, and then the whole table is rebuilt with
    # NOT NULL + DEFAULT. PostgreSQL (the production database) gets the
    # constraint applied in place. Existing windows are backdated to the
    # UTC midnight of their window day instead of the migration time, so
    # the hourly retention keeps working on old rows from day one.
    op.add_column(
        "rate_limit_windows",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE rate_limit_windows"
            " SET created_at = timezone('UTC', window_date::timestamp)"
        )
        op.create_index(
            "ix_rate_limit_windows_created_at", "rate_limit_windows", ["created_at"]
        )
        op.execute(
            "ALTER TABLE rate_limit_windows"
            " ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP,"
            " ALTER COLUMN created_at SET NOT NULL"
        )
    else:
        op.execute("UPDATE rate_limit_windows SET created_at = datetime(window_date)")
        # Batch mode rebuilds the table copying the backfilled rows, which
        # enforces NOT NULL and the DEFAULT again for raw SQL writers (the
        # ORM default alone only covered SQLAlchemy inserts).
        with op.batch_alter_table(
            "rate_limit_windows",
            copy_from=_sqlite_table(),
            recreate="always",
        ):
            pass
        op.create_index(
            "ix_rate_limit_windows_created_at", "rate_limit_windows", ["created_at"]
        )


def downgrade() -> None:
    op.drop_index("ix_rate_limit_windows_created_at", table_name="rate_limit_windows")
    op.drop_column("rate_limit_windows", "created_at")
