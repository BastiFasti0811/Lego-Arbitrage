"""add valuation_runs, valuation_run_items and inventory_items.reference_url

Der Bewertungslauf meldete `errors: 0` bei 32 unbewerteten Sets, weil jede
uebersprungene Bewertung ein nacktes `continue` war. Diese Tabellen halten je
Lauf und je Set fest, was passiert ist und welche Quelle was geliefert hat.

Reines DDL, keine Datenlogik — die Tabellen sind beim Aufspielen leer, und
`reference_url` ist nullable.

Revision ID: d3a91c2f80b7
Revises: b8e5c30d7f14
Create Date: 2026-08-25 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3a91c2f80b7"
down_revision: str | None = "b8e5c30d7f14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuation_runs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("items_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("items_valued", sa.Integer(), server_default="0", nullable=False),
        sa.Column("items_skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("items_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "valuation_run_items",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("set_number", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("consensus_price", sa.Float(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        # Cascade, weil das Aufraeumen alter Laeufe sonst verwaiste Zeilen laesst.
        sa.ForeignKeyConstraint(["run_id"], ["valuation_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_valuation_run_items_run_id", "valuation_run_items", ["run_id"])
    op.add_column("inventory_items", sa.Column("reference_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("inventory_items", "reference_url")
    op.drop_index("ix_valuation_run_items_run_id", table_name="valuation_run_items")
    op.drop_table("valuation_run_items")
    op.drop_table("valuation_runs")
