"""Collapse duplicate offer rows and pin one row per listing.

Search-result URLs carried per-request tracking tokens, so the
`(platform, offer_url)` upsert key never matched and every scrape run inserted
the same listing again. This rewrites the stored URLs to their canonical form,
keeps the freshest row per listing, and adds the unique constraint that stops
the problem from coming back.

deal_feedback points at offers with ON DELETE SET NULL, so its offer_id is
moved to the surviving row before the duplicates go — losing which offer a
rating belonged to would quietly degrade the training data.

DESTRUCTIVE: the dropped rows are gone. The downgrade removes the constraint
but cannot bring them back. They are redundant copies — the surviving row of
each group carries the same listing with the most recent analysis.

Revision ID: a7d2e4c91f65
Revises: c4f2a91b7d3e
"""

import sqlalchemy as sa

from alembic import op
from app.domain.offer_url import plan_duplicate_cleanup

revision = "a7d2e4c91f65"
down_revision = "c4f2a91b7d3e"
branch_labels = None
depends_on = None

_CONSTRAINT = "uq_offers_set_platform_url"


def upgrade() -> None:
    collapse_duplicates(op.get_bind())
    op.create_unique_constraint(_CONSTRAINT, "offers", ["set_id", "platform", "offer_url"])


def collapse_duplicates(connection) -> None:
    """Data half of the migration, kept separate so it can be tested."""
    rows = connection.execute(
        sa.text("SELECT id, set_id, platform, offer_url, last_seen_at FROM offers ORDER BY id")
    ).all()

    for group in plan_duplicate_cleanup(rows):
        if group.drop_ids:
            connection.execute(
                sa.text(
                    "UPDATE deal_feedback SET offer_id = :keep WHERE offer_id IN :drop"
                ).bindparams(sa.bindparam("drop", expanding=True)),
                {"keep": group.keep_id, "drop": list(group.drop_ids)},
            )
            connection.execute(
                sa.text("DELETE FROM offers WHERE id IN :drop").bindparams(
                    sa.bindparam("drop", expanding=True)
                ),
                {"drop": list(group.drop_ids)},
            )

        connection.execute(
            sa.text("UPDATE offers SET offer_url = :url WHERE id = :id AND offer_url <> :url"),
            {"url": group.canonical_url, "id": group.keep_id},
        )

    # Rows without a URL have no identity, so plan_duplicate_cleanup leaves them
    # alone — but two of them under the same set and platform would collide with
    # the constraint below. The upsert refuses to write such rows; should any
    # remain from an earlier schema, keep the newest and drop the rest.
    urlless = connection.execute(
        sa.text(
            "SELECT id, set_id, platform, last_seen_at FROM offers "
            "WHERE offer_url IS NULL OR offer_url = '' ORDER BY id"
        )
    ).all()
    newest_per_group: dict[tuple, tuple] = {}
    for offer_id, set_id, platform, last_seen_at in urlless:
        key = (set_id, platform)
        current = newest_per_group.get(key)
        rank = (last_seen_at is not None, last_seen_at, offer_id)
        if current is None or rank > current[1]:
            if current is not None:
                _drop_offer(connection, current[0])
            newest_per_group[key] = (offer_id, rank)
        else:
            _drop_offer(connection, offer_id)


def _drop_offer(connection, offer_id: int) -> None:
    connection.execute(
        sa.text("UPDATE deal_feedback SET offer_id = NULL WHERE offer_id = :id"), {"id": offer_id}
    )
    connection.execute(sa.text("DELETE FROM offers WHERE id = :id"), {"id": offer_id})


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "offers", type_="unique")
