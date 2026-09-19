"""KI-Preisrahmen-Spalten fuer die Foto-Analyse + DRAFT-Item-Status (PR 2).

Drei nullable ADD COLUMNs, kein Backfill, kein Index: bestehende Zeilen
bleiben mit NULL, DRAFT ist ein reiner Enum-Wert im Python-Code und braucht
keine Schemaaenderung fuer die status-Spalte (String(20), kein DB-Constraint).

Revision ID: f7c3e91a54d2
Revises: d4e8a12f9c30
Create Date: 2026-09-01 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7c3e91a54d2"
down_revision: str | None = "d4e8a12f9c30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(sa.Column("ai_price_min", sa.Float(), nullable=True))
        batch.add_column(sa.Column("ai_price_max", sa.Float(), nullable=True))
        batch.add_column(sa.Column("ai_analysis_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_column("ai_analysis_at")
        batch.drop_column("ai_price_max")
        batch.drop_column("ai_price_min")
