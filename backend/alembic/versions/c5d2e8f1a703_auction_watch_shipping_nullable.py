"""Versandkosten einer beobachteten Auktion duerfen unbekannt sein.

Bisher NOT NULL mit 0.0 als Ersatz: ein unbekannter Versand sah aus wie
kostenloser, und die Sperre "Versandkosten fehlen" in evaluate_auction griff
fuer gespeicherte Watches nie. Bestehende Zeilen bleiben bei ihrem Wert
(0.0 ist dort nicht von "unbekannt" zu unterscheiden, kein Backfill).

Revision ID: c5d2e8f1a703
Revises: a91c07e54b22
"""

import sqlalchemy as sa

from alembic import op

revision = "c5d2e8f1a703"
down_revision = "a91c07e54b22"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("auction_watch_items") as batch:
        batch.alter_column("purchase_shipping", existing_type=sa.Float(), nullable=True)


def downgrade():
    op.execute("UPDATE auction_watch_items SET purchase_shipping = 0 WHERE purchase_shipping IS NULL")
    with op.batch_alter_table("auction_watch_items") as batch:
        batch.alter_column("purchase_shipping", existing_type=sa.Float(), nullable=False)
