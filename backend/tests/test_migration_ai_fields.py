"""Migration f7c3e91a54d2 legt die drei ai_-Spalten der KI-Fotoanalyse an (PR 2).

Einfache ADD-COLUMN-Migration ohne Backfill/Index — trotzdem gegen eine echte
Bestandszeile getestet (Muster test_migration_listings.py), damit sicher ist,
dass die neuen Spalten nullable sind und bestehende Zeilen NULL bekommen.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "f7c3e91a54d2_ai_felder_und_draft_status.py"
)

OLD_INVENTORY_DDL = """
CREATE TABLE inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_number VARCHAR(20),
    set_name VARCHAR(300) NOT NULL,
    buy_price FLOAT,
    buy_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_f7c3e91a54d2", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated():
    """SQLite-DB im Vorher-Zustand mit einer echten Zeile, dann migriert."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(OLD_INVENTORY_DDL))
        conn.execute(sa.text(
            "INSERT INTO inventory_items (set_number, set_name, buy_price, buy_date, status)"
            " VALUES ('75331', 'Razor Crest', 480.0, '2026-03-01', 'HOLDING')"
        ))

        context = MigrationContext.configure(conn)
        module = _load_migration()
        module.op = Operations(context)
        module.upgrade()
        yield conn


def test_ai_columns_exist_and_are_nullable(migrated):
    columns = {c["name"]: c for c in inspect(migrated).get_columns("inventory_items")}
    for name in ("ai_price_min", "ai_price_max", "ai_analysis_at"):
        assert name in columns, f"Spalte {name} fehlt"
        assert columns[name]["nullable"] is True, f"Spalte {name} muss nullable sein"


def test_existing_row_gets_null_ai_values(migrated):
    row = migrated.execute(sa.text(
        "SELECT ai_price_min, ai_price_max, ai_analysis_at FROM inventory_items WHERE set_number = '75331'"
    )).one()
    assert row.ai_price_min is None
    assert row.ai_price_max is None
    assert row.ai_analysis_at is None
