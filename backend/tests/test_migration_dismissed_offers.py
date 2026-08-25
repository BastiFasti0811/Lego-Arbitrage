"""Die Abwahl-Migration muss die Tabelle bauen, die das Modell erwartet.

CI fährt `alembic upgrade head` gegen eine leere Datenbank — das beweist nur,
dass die Migration durchläuft, nicht dass sie zum Modell passt. Eine vergessene
Spalte fällt dort nicht auf und schlägt erst beim ersten INSERT auf Prod zu.
Darum hier der Abgleich gegen `DismissedOffer.__table__`.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.models.dismissal import DismissedOffer

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "b8e5c30d7f14_add_dismissed_offers.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_b8e5c30d7f14", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated():
    """Eine SQLite-DB, auf der die Migration gelaufen ist."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        module = _load_migration()
        module.op = Operations(context)
        module.upgrade()
        yield conn


def test_the_table_is_named_like_the_model(migrated):
    assert DismissedOffer.__tablename__ in inspect(migrated).get_table_names()


def test_every_model_column_exists(migrated):
    created = {column["name"] for column in inspect(migrated).get_columns(DismissedOffer.__tablename__)}
    expected = {column.name for column in DismissedOffer.__table__.columns}

    assert expected - created == set(), "Spalten fehlen in der Migration"
    assert created - expected == set(), "die Migration legt Spalten an, die das Modell nicht kennt"


def test_column_types_and_nullability_match_the_model(migrated):
    # Nur Namen zu vergleichen liesse eine falsche Laenge oder ein vergessenes
    # NOT NULL durch — und genau die schlagen erst beim ersten INSERT zu.
    # SQLite erzwingt VARCHAR-Laengen nicht, der DDL-Text nennt sie aber.
    created = {column["name"]: column for column in inspect(migrated).get_columns(DismissedOffer.__tablename__)}

    for column in DismissedOffer.__table__.columns:
        actual = created[column.name]
        assert str(actual["type"]).upper() == str(column.type).upper(), (
            f"{column.name}: Migration hat {actual['type']}, Modell erwartet {column.type}"
        )
        # Spalten mit server_default sind in der Migration NOT NULL, im Modell
        # aber ueber den Default befuellt — nur die uebrigen vergleichen.
        if column.server_default is None:
            assert actual["nullable"] == column.nullable, f"{column.name}: Nullability weicht ab"



def test_the_identity_is_unique(migrated):
    # Ohne diesen Index wäre die Abwahl nicht idempotent: `on_conflict_do_nothing`
    # braucht die Unique-Constraint, an der es sich aufhängen kann.
    indexes = inspect(migrated).get_indexes(DismissedOffer.__tablename__)
    identity_indexes = [ix for ix in indexes if ix["column_names"] == ["offer_identity"]]

    # SQLite meldet 1/0 statt True/False — darum auf Wahrheitswert prüfen.
    assert identity_indexes, "kein Index auf offer_identity"
    assert identity_indexes[0]["unique"], "der Index auf offer_identity ist nicht unique"


def test_downgrade_drops_the_table_again(migrated):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _load_migration()
    module.op = Operations(MigrationContext.configure(migrated))
    module.downgrade()

    assert DismissedOffer.__tablename__ not in inspect(migrated).get_table_names()
