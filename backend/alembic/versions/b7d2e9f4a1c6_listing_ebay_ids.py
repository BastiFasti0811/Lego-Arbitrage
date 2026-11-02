"""eBay Sell API: SKU, Angebot und Artikelnummer am Listing (Nachtrag ADR 0002).

Reine Schema-Migration: drei nullable Spalten und ein Unique-Index auf der
noch leeren SKU-Spalte, kein Backfill. Der laufende Code vor diesem Deploy
kennt die Spalten nicht und fasst sie nicht an.

Revision ID: b7d2e9f4a1c6
Revises: a9f3c5e2d7b1
"""

import sqlalchemy as sa

from alembic import op

revision = "b7d2e9f4a1c6"
down_revision = "a9f3c5e2d7b1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("listings", sa.Column("ebay_sku", sa.String(50), nullable=True))
    op.add_column("listings", sa.Column("ebay_offer_id", sa.String(40), nullable=True))
    op.add_column("listings", sa.Column("ebay_listing_id", sa.String(40), nullable=True))
    op.create_index("uq_listings_ebay_sku", "listings", ["ebay_sku"], unique=True)


def downgrade():
    op.drop_index("uq_listings_ebay_sku", table_name="listings")
    op.drop_column("listings", "ebay_listing_id")
    op.drop_column("listings", "ebay_offer_id")
    op.drop_column("listings", "ebay_sku")
