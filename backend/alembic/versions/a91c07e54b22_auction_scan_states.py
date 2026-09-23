"""Persist auction discovery results and notification identities.

Revision ID: a91c07e54b22
Revises: a3f6c8d1b492
"""

import sqlalchemy as sa

from alembic import op

revision = "a91c07e54b22"
down_revision = "a3f6c8d1b492"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auction_scan_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("notified_urls", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform"),
    )


def downgrade():
    op.drop_table("auction_scan_states")
