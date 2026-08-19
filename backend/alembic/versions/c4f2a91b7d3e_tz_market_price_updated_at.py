"""Convert market_price_updated_at columns to timestamptz.

scrape_daily and deal_analysis write timezone-aware datetimes (UTC); asyncpg
rejects aware values for TIMESTAMP WITHOUT TIME ZONE, rolling back the whole
per-set scrape transaction. Existing naive values were written as UTC.

Revision ID: c4f2a91b7d3e
Revises: 9f3c1a7b2e84
"""

import sqlalchemy as sa

from alembic import op

revision = "c4f2a91b7d3e"
down_revision = "9f3c1a7b2e84"
branch_labels = None
depends_on = None

_COLUMNS = (("lego_sets", "market_price_updated_at"), ("inventory_items", "market_price_updated_at"))


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(timezone=False),
            existing_nullable=True,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=False),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=True,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
