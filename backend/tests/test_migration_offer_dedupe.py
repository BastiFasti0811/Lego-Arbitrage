"""The cleanup migration runs against real rows before it touches production.

It deletes offer rows, so the behaviour is verified here against an actual
database rather than argued from the code: duplicates collapse, the freshest
row survives with a canonical URL, and feedback follows the survivor instead
of losing its reference.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.models.feedback import DealFeedback

# Taken from the model, not typed by hand: the first version of this migration
# said "feedback" because that is the module name, while the table is
# deal_feedback. CI never caught it — its database is empty, so the branch that
# touches the table is only reached when duplicates actually exist.
FEEDBACK_TABLE = DealFeedback.__tablename__

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "a7d2e4c91f65_dedupe_offers_by_canonical_url.py"
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
RUN_1 = "https://www.amazon.de/42143-Ferrari/dp/B09QFSCWD9/ref=sr_1_1?dib=eyJ2IjoiMSJ9.AAA&qid=1755640000"
RUN_2 = "https://www.amazon.de/42143-Ferrari/dp/B09QFSCWD9/ref=sr_1_5?dib=eyJ2IjoiMSJ9.ZZZ&qid=1755647777"
CANONICAL = "https://www.amazon.de/dp/B09QFSCWD9"
OTHER = "https://www.amazon.de/dp/B09XVMSWJC"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_a7d2e4c91f65", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def connection():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE offers (id INTEGER PRIMARY KEY, set_id INTEGER, platform TEXT, "
                "offer_url TEXT, last_seen_at TIMESTAMP)"
            )
        )
        conn.execute(
            text(f"CREATE TABLE {FEEDBACK_TABLE} (id INTEGER PRIMARY KEY, offer_id INTEGER)")
        )
        yield conn


def _insert(conn, offer_id, url, age_hours=0, set_id=1, platform="AMAZON"):
    conn.execute(
        text(
            "INSERT INTO offers (id, set_id, platform, offer_url, last_seen_at) "
            "VALUES (:id, :set_id, :platform, :url, :seen)"
        ),
        {
            "id": offer_id,
            "set_id": set_id,
            "platform": platform,
            "url": url,
            "seen": NOW - timedelta(hours=age_hours),
        },
    )


def _offers(conn):
    return conn.execute(text("SELECT id, offer_url FROM offers ORDER BY id")).all()


def test_duplicates_collapse_to_the_freshest_row(connection):
    _insert(connection, 1, RUN_1, age_hours=6)
    _insert(connection, 2, RUN_2, age_hours=1)
    _insert(connection, 3, CANONICAL, age_hours=4)

    _load_migration().collapse_duplicates(connection)

    assert _offers(connection) == [(2, CANONICAL)]


def test_distinct_listings_are_untouched(connection):
    _insert(connection, 1, RUN_1)
    _insert(connection, 2, OTHER)

    _load_migration().collapse_duplicates(connection)

    assert _offers(connection) == [(1, CANONICAL), (2, OTHER)]


def test_same_url_under_two_sets_survives_twice(connection):
    _insert(connection, 1, RUN_1, set_id=1)
    _insert(connection, 2, RUN_2, set_id=2)

    _load_migration().collapse_duplicates(connection)

    assert _offers(connection) == [(1, CANONICAL), (2, CANONICAL)]


def test_feedback_follows_the_surviving_row(connection):
    _insert(connection, 1, RUN_1, age_hours=6)
    _insert(connection, 2, RUN_2, age_hours=1)
    connection.execute(
        text(f"INSERT INTO {FEEDBACK_TABLE} (id, offer_id) VALUES (10, 1), (11, 2)")
    )

    _load_migration().collapse_duplicates(connection)

    remaining = connection.execute(
        text(f"SELECT id, offer_id FROM {FEEDBACK_TABLE} ORDER BY id")
    ).all()
    assert remaining == [(10, 2), (11, 2)], "the rating for the dropped copy points at the survivor"


def test_surplus_rows_without_url_are_reduced_to_one(connection):
    # They would collide with the unique constraint the migration adds.
    _insert(connection, 1, "", age_hours=5)
    _insert(connection, 2, None, age_hours=1)

    _load_migration().collapse_duplicates(connection)

    assert [row[0] for row in _offers(connection)] == [2]


def test_url_less_row_alone_is_kept(connection):
    _insert(connection, 1, "", age_hours=5)

    _load_migration().collapse_duplicates(connection)

    assert [row[0] for row in _offers(connection)] == [1]


def test_running_twice_changes_nothing(connection):
    _insert(connection, 1, RUN_1, age_hours=6)
    _insert(connection, 2, RUN_2, age_hours=1)
    migration = _load_migration()

    migration.collapse_duplicates(connection)
    first = _offers(connection)
    migration.collapse_duplicates(connection)

    assert _offers(connection) == first
