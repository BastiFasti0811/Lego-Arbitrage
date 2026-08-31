"""Inventar fuer alles (PR 1): item_type/product_group/search_query,
set_number+buy_price nullable, listings + listing_price_changes,
Cleanup der nie gelesenen Verkaufs-Credentials (ADR 0002).

Backfill: Bestand ist ausnahmslos Lego — item_type/product_group kommen
per server_default, search_query wird aus der Set-Nummer erzeugt.

Revision ID: d4e8a12f9c30
Revises: d3a91c2f80b7
Create Date: 2026-08-30 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e8a12f9c30"
down_revision: str | None = "d3a91c2f80b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN_STATUS_SQL = sa.text("status IN ('DRAFT','ACTIVE','PAUSED')")


def upgrade() -> None:
    # --- inventory_items generalisieren (batch: laeuft auf SQLite und Postgres)
    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(sa.Column("item_type", sa.String(length=20), nullable=False, server_default="LEGO"))
        batch.add_column(sa.Column("product_group", sa.String(length=100), nullable=False, server_default="Lego"))
        batch.add_column(sa.Column("search_query", sa.String(length=300), nullable=True))
        batch.alter_column("set_number", existing_type=sa.String(length=20), nullable=True)
        batch.alter_column("buy_price", existing_type=sa.Float(), nullable=True)

    op.execute("UPDATE inventory_items SET search_query = 'LEGO ' || set_number WHERE set_number IS NOT NULL")

    # --- listings
    op.create_table(
        "listings",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("price_type", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("platform_category", sa.String(length=200), nullable=True),
        sa.Column("listed_at", sa.Date(), nullable=True),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("check_interval_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("price_drop_percent", sa.Float(), nullable=False, server_default="10"),
        sa.Column("min_price", sa.Float(), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("floor_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suggested_price", sa.Float(), nullable=True),
        sa.Column("suggestion_reason", sa.Text(), nullable=True),
        sa.Column("suggestion_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["item_id"], ["inventory_items.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_listings_item_id", "listings", ["item_id"])
    op.create_index(
        "uq_listings_open_per_platform",
        "listings",
        ["item_id", "platform"],
        unique=True,
        sqlite_where=_OPEN_STATUS_SQL,
        postgresql_where=_OPEN_STATUS_SQL,
    )

    # --- listing_price_changes
    op.create_table(
        "listing_price_changes",
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("old_price", sa.Float(), nullable=False),
        sa.Column("new_price", sa.Float(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_listing_price_changes_listing_id", "listing_price_changes", ["listing_id"])

    # --- nie gelesene Verkaufs-Credentials entfernen (ADR 0002)
    op.execute(
        "DELETE FROM app_settings WHERE key IN "
        "('ebay_api_key','ebay_api_secret','kleinanzeigen_email','kleinanzeigen_password')"
    )


def downgrade() -> None:
    op.drop_index("ix_listing_price_changes_listing_id", table_name="listing_price_changes")
    op.drop_table("listing_price_changes")
    op.drop_index("uq_listings_open_per_platform", table_name="listings")
    op.drop_index("ix_listings_item_id", table_name="listings")
    op.drop_table("listings")
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_column("search_query")
        batch.drop_column("product_group")
        batch.drop_column("item_type")
        batch.alter_column("buy_price", existing_type=sa.Float(), nullable=False)
        batch.alter_column("set_number", existing_type=sa.String(length=20), nullable=False)
