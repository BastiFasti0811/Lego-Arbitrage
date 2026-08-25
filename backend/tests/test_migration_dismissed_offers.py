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


def test_die_tabelle_heisst_wie_im_modell(migrated):
    assert DismissedOffer.__tablename__ in inspect(migrated).get_table_names()


def test_alle_spalten_des_modells_existieren(migrated):
    created = {column["name"] for column in inspect(migrated).get_columns(DismissedOffer.__tablename__)}
    expected = {column.name for column in DismissedOffer.__table__.columns}

    assert expected - created == set(), "Spalten fehlen in der Migration"


def test_die_identitaet_ist_eindeutig(migrated):
    # Ohne diesen Index wäre die Abwahl nicht idempotent: `on_conflict_do_nothing`
    # braucht die Unique-Constraint, an der es sich aufhängen kann.
    indexes = inspect(migrated).get_indexes(DismissedOffer.__tablename__)
    identity_indexes = [ix for ix in indexes if ix["column_names"] == ["offer_identity"]]

    # SQLite meldet 1/0 statt True/False — darum auf Wahrheitswert prüfen.
    assert identity_indexes, "kein Index auf offer_identity"
    assert identity_indexes[0]["unique"], "der Index auf offer_identity ist nicht unique"


def test_downgrade_raeumt_die_tabelle_wieder_ab(migrated):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _load_migration()
    module.op = Operations(MigrationContext.configure(migrated))
    module.downgrade()

    assert DismissedOffer.__tablename__ not in inspect(migrated).get_table_names()
