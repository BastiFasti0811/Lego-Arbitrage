"""Kaufentscheidungen aus dem Telegram-Alert (Gekauft/Abgelehnt).

Reine Schema-Migration: neue Tabelle, kein Backfill. Der laufende Code vor
diesem Deploy kennt die Tabelle nicht und fasst sie nicht an.

Revision ID: c2e7d4a91b58
Revises: b6d1f0a4c7e3
"""

import sqlalchemy as sa

from alembic import op

revision = "c2e7d4a91b58"
down_revision = "b6d1f0a4c7e3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "offer_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("offer_identity", sa.String(600), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("offer_url", sa.Text(), nullable=False),
        sa.Column("set_number", sa.String(20), nullable=True),
        sa.Column("price_eur", sa.Float(), nullable=True),
        sa.Column("recommendation", sa.String(20), nullable=True),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dismissed_here", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_offer_decisions_offer_identity", "offer_decisions", ["offer_identity"], unique=True)
    op.create_index("ix_offer_decisions_decided_at", "offer_decisions", ["decided_at"])


def downgrade():
    op.drop_index("ix_offer_decisions_decided_at", table_name="offer_decisions")
    op.drop_index("ix_offer_decisions_offer_identity", table_name="offer_decisions")
    op.drop_table("offer_decisions")
