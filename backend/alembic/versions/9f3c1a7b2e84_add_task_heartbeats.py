"""add task_heartbeats

Revision ID: 9f3c1a7b2e84
Revises: fd1b56b5db58
Create Date: 2026-06-29 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f3c1a7b2e84"
down_revision: str | None = "fd1b56b5db58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_heartbeats",
        sa.Column("task_name", sa.String(length=200), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=20), server_default="unknown", nullable=False),
        sa.Column("last_detail", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_heartbeats_task_name", "task_heartbeats", ["task_name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_task_heartbeats_task_name", table_name="task_heartbeats")
    op.drop_table("task_heartbeats")
