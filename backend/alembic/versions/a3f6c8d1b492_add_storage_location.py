"""Lagerort-Feld fuer Inventarposten (Freitext, z. B. "Dachboden Kiste 3").

Eine nullable ADD COLUMN, kein Backfill, kein Index: bestehende Zeilen bleiben
mit NULL, bis sie beim naechsten Eingang oder von Hand befuellt werden.

Revision ID: a3f6c8d1b492
Revises: f7c3e91a54d2
Create Date: 2026-09-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3f6c8d1b492"
down_revision: str | None = "f7c3e91a54d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(sa.Column("storage_location", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_column("storage_location")
