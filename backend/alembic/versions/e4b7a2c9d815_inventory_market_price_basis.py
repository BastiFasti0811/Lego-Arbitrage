"""Herkunft des Marktpreises eines Inventarpostens (gruen/gelb im Frontend).

"CONSENSUS" = mindestens zwei uebereinstimmende Quellen, "BRICKMERGE_ONLY" =
nur BrickMerge-Bestpreis, "EBAY_SOLD" = manuelle Neubewertung ueber belastbare
eBay-Verkaeufe. Nullable ADD COLUMN ohne Backfill: bestehende Preise bleiben
ohne Markierung, bis der naechste Bewertungslauf sie setzt.

Revision ID: e4b7a2c9d815
Revises: c5d2e8f1a703
"""

import sqlalchemy as sa

from alembic import op

revision = "e4b7a2c9d815"
down_revision = "c5d2e8f1a703"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(sa.Column("market_price_basis", sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_column("market_price_basis")
