"""Einstell-Auftraege fuer Kleinanzeigen per Browser (Nachtrag ADR 0002).

Reine Schema-Migration: neue Tabelle, kein Backfill. Der laufende Code vor
diesem Deploy kennt die Tabelle nicht und fasst sie nicht an.

Revision ID: a9f3c5e2d7b1
Revises: c2e7d4a91b58
"""

import sqlalchemy as sa

from alembic import op

revision = "a9f3c5e2d7b1"
down_revision = "c2e7d4a91b58"
branch_labels = None
depends_on = None

_OPEN_JOB_SQL = sa.text("status IN ('REQUESTED','APPROVED','IN_PROGRESS')")


def upgrade():
    op.create_table(
        "listing_posting_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("dry_run", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("approved_by", sa.String(20), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_listing_posting_jobs_listing_id", "listing_posting_jobs", ["listing_id"])
    op.create_index("ix_listing_posting_jobs_status", "listing_posting_jobs", ["status"])
    op.create_index(
        "uq_listing_posting_jobs_open",
        "listing_posting_jobs",
        ["listing_id"],
        unique=True,
        sqlite_where=_OPEN_JOB_SQL,
        postgresql_where=_OPEN_JOB_SQL,
    )


def downgrade():
    op.drop_index("uq_listing_posting_jobs_open", table_name="listing_posting_jobs")
    op.drop_index("ix_listing_posting_jobs_status", table_name="listing_posting_jobs")
    op.drop_index("ix_listing_posting_jobs_listing_id", table_name="listing_posting_jobs")
    op.drop_table("listing_posting_jobs")
