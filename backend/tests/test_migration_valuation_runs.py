"""Die Protokoll-Migration muss die Tabellen bauen, die die Modelle erwarten.

CI faehrt `alembic upgrade head` gegen eine leere Datenbank — das beweist nur,
dass die Migration durchlaeuft, nicht dass sie zum Modell passt. Eine vergessene
Spalte faellt dort nicht auf und schlaegt erst beim ersten INSERT auf Prod zu.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

from app.models.inventory import InventoryItem
from app.models.valuation_run import ValuationRun, ValuationRunItem

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "d3a91c2f80b7_add_valuation_runs.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_d3a91c2f80b7", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        # Die Migration haengt `reference_url` an eine bestehende Tabelle —
        # die muss es geben, sonst prueft der Test die falsche Sache. Das
        # Modell hat die Spalte inzwischen selbst (dieselbe Aufgabe fuegt sie
        # hinzu) — fuer den Vorher-Zustand darum jede Spalte AUSSER der, die
        # diese Migration erst per ADD COLUMN bringt. Sonst kollidiert das
        # ALTER TABLE mit einer bereits vorhandenen Spalte gleichen Namens.
        pre_migration_inventory = sa.Table(
            "inventory_items",
            sa.MetaData(),
            *(
                sa.Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable)
                for c in InventoryItem.__table__.columns
                if c.name != "reference_url"
            ),
        )
        pre_migration_inventory.create(conn)
        context = MigrationContext.configure(conn)
        module = _load_migration()
        module.op = Operations(context)
        module.upgrade()
        yield conn


def test_both_tables_exist(migrated):
    names = inspect(migrated).get_table_names()
    assert ValuationRun.__tablename__ in names
    assert ValuationRunItem.__tablename__ in names


@pytest.mark.parametrize("model", [ValuationRun, ValuationRunItem])
def test_every_model_column_exists_in_the_migration(migrated, model):
    actual = {c["name"] for c in inspect(migrated).get_columns(model.__tablename__)}
    expected = {c.name for c in model.__table__.columns}
    assert expected <= actual, f"fehlend: {sorted(expected - actual)}"


def test_inventory_gained_the_reference_url(migrated):
    actual = {c["name"] for c in inspect(migrated).get_columns(InventoryItem.__tablename__)}
    assert "reference_url" in actual


def test_run_items_are_removed_with_their_run(migrated):
    # Nachgemessen: der SQLite-Inspector liefert fuer diesen Fremdschluessel
    # `options == {}` — die Cascade-Aussage steht nur im DDL selbst. Ein Test
    # ueber get_foreign_keys() waere gruen, ohne irgendetwas zu pruefen.
    ddl = migrated.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE name = :name",
        {"name": ValuationRunItem.__tablename__},
    ).scalar_one()
    # Ohne Cascade bleiben beim Aufraeumen alter Laeufe verwaiste Zeilen stehen.
    assert "ON DELETE CASCADE" in ddl.upper()
