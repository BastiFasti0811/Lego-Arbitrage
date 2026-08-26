"""add dismissed_offers

Der Feed sortiert nach opportunity_score und kappt bei 20 — ein Fund mit
+121 % ROI stand damit Lauf für Lauf auf Platz 1, ohne dass man ihn loswerden
konnte. Diese Tabelle hält die abgewählten Inserate.

Der Schlüssel ist die Identität aus `offer_identity()` (PLATTFORM:kanonische
URL), nicht die offer_id: die Dedupe-Bereinigung löscht Offer-Zeilen und der
nächste Scrape legt sie neu an.

Reines DDL, keine Datenlogik — die Tabelle ist beim Aufspielen leer.

Revision ID: b8e5c30d7f14
Revises: a7d2e4c91f65
Create Date: 2026-08-25 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e5c30d7f14"
down_revision: str | None = "a7d2e4c91f65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dismissed_offers",
        sa.Column("offer_identity", sa.String(length=600), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("offer_url", sa.Text(), nullable=False),
        sa.Column("offer_title", sa.String(length=500), nullable=True),
        sa.Column("set_number", sa.String(length=20), nullable=True),
        sa.Column("price_eur", sa.Float(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique, weil `on_conflict_do_nothing(index_elements=["offer_identity"])`
    # sich genau hier aufhängt — ohne den Index ist die Abwahl nicht idempotent.
    op.create_index("ix_dismissed_offers_offer_identity", "dismissed_offers", ["offer_identity"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_dismissed_offers_offer_identity", table_name="dismissed_offers")
    op.drop_table("dismissed_offers")
