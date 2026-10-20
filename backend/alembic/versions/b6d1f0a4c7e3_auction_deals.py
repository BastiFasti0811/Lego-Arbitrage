"""Auktionslose mit Gebotsfreigabe fuer den Deal-Zaehler (Wochen-Digest).

Reine Schema-Migration: neue Tabelle, kein Backfill. Freigaben vor dem Deploy
stehen nirgends mit Zeitstempel und bleiben ungezaehlt.

Revision ID: b6d1f0a4c7e3
Revises: e4b7a2c9d815
"""

import sqlalchemy as sa

from alembic import op

revision = "b6d1f0a4c7e3"
down_revision = "e4b7a2c9d815"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auction_deals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("set_number", sa.String(20), nullable=True),
        sa.Column("lot_title", sa.String(500), nullable=True),
        sa.Column("current_bid", sa.Float(), nullable=True),
        sa.Column("max_bid", sa.Float(), nullable=True),
        sa.Column("expected_profit", sa.Float(), nullable=True),
        sa.Column("found_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "source_url", name="uq_auction_deals_platform_url"),
    )
    op.create_index("ix_auction_deals_found_at", "auction_deals", ["found_at"])


def downgrade():
    op.drop_index("ix_auction_deals_found_at", table_name="auction_deals")
    op.drop_table("auction_deals")
