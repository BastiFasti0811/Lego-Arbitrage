"""Die PR-1-Migration muss Schema UND Bestandsdaten korrekt behandeln.

CI faehrt `alembic upgrade head` nur gegen eine leere DB. Der Backfill
(item_type/product_group/search_query fuer Bestands-Lego) und der
app_settings-Cleanup sind Datenlogik — darum hier gegen echte Zeilen.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

from app.models.listing import Listing, ListingPriceChange

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "d4e8a12f9c30_inventar_fuer_alles_pr1.py"

OLD_INVENTORY_DDL = """
CREATE TABLE inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_number VARCHAR(20) NOT NULL,
    set_name VARCHAR(300) NOT NULL,
    buy_price FLOAT NOT NULL,
    buy_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

OLD_SETTINGS_DDL = """
CREATE TABLE app_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key VARCHAR(100) NOT NULL,
    value TEXT
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_d4e8a12f9c30", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated():
    """SQLite-DB im Vorher-Zustand mit echten Zeilen, dann migriert."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(OLD_INVENTORY_DDL))
        conn.execute(sa.text(OLD_SETTINGS_DDL))
        conn.execute(sa.text(
            "INSERT INTO inventory_items (set_number, set_name, buy_price, buy_date, status)"
            " VALUES ('75331', 'Razor Crest', 480.0, '2026-03-01', 'HOLDING')"
        ))
        conn.execute(sa.text("INSERT INTO app_settings (key, value) VALUES ('kleinanzeigen_password', 'geheim')"))
        conn.execute(sa.text("INSERT INTO app_settings (key, value) VALUES ('telegram_bot_token', 'bleibt')"))

        context = MigrationContext.configure(conn)
        module = _load_migration()
        module.op = Operations(context)
        module.upgrade()
        yield conn


def test_new_tables_exist(migrated):
    tables = inspect(migrated).get_table_names()
    assert Listing.__tablename__ in tables
    assert ListingPriceChange.__tablename__ in tables


@pytest.mark.parametrize("model", [Listing, ListingPriceChange])
def test_every_model_column_exists(migrated, model):
    created = {column["name"] for column in inspect(migrated).get_columns(model.__tablename__)}
    expected = {column.name for column in model.__table__.columns}
    assert expected - created == set(), "Spalten fehlen in der Migration"
    assert created - expected == set(), "die Migration legt Spalten an, die das Modell nicht kennt"


@pytest.mark.parametrize("model", [Listing, ListingPriceChange])
def test_column_types_and_nullability_match_the_model(migrated, model):
    created = {column["name"]: column for column in inspect(migrated).get_columns(model.__tablename__)}
    for column in model.__table__.columns:
        actual = created[column.name]
        assert str(actual["type"]).upper() == str(column.type).upper(), (
            f"{column.name}: Migration hat {actual['type']}, Modell erwartet {column.type}"
        )
        if column.server_default is None:
            assert actual["nullable"] == column.nullable, f"{column.name}: Nullability weicht ab"


def test_open_listing_unique_index_is_partial_and_unique(migrated):
    indexes = inspect(migrated).get_indexes(Listing.__tablename__)
    open_ix = [ix for ix in indexes if ix["name"] == "uq_listings_open_per_platform"]
    assert open_ix, "Partial-Unique-Index uq_listings_open_per_platform fehlt"
    assert open_ix[0]["unique"]
    assert open_ix[0]["column_names"] == ["item_id", "platform"]


def test_inventory_gets_new_columns_with_backfill(migrated):
    row = migrated.execute(sa.text(
        "SELECT item_type, product_group, search_query FROM inventory_items WHERE set_number = '75331'"
    )).one()
    assert row.item_type == "LEGO"
    assert row.product_group == "Lego"
    assert row.search_query == "LEGO 75331"


def test_set_number_and_buy_price_become_nullable(migrated):
    columns = {c["name"]: c for c in inspect(migrated).get_columns("inventory_items")}
    assert columns["set_number"]["nullable"] is True
    assert columns["buy_price"]["nullable"] is True


def test_sales_credentials_are_deleted_but_others_survive(migrated):
    keys = {r.key for r in migrated.execute(sa.text("SELECT key FROM app_settings")).all()}
    assert "kleinanzeigen_password" not in keys
    assert "telegram_bot_token" in keys


def test_two_open_listings_on_same_platform_are_rejected(migrated):
    insert = sa.text(
        "INSERT INTO listings (item_id, platform, status, price_type, check_interval_days, price_drop_percent)"
        " VALUES (1, 'KLEINANZEIGEN', :status, 'VB', 14, 10)"
    )
    migrated.execute(insert.bindparams(status="ACTIVE"))
    migrated.execute(insert.bindparams(status="ENDED"))  # Historie kollidiert nicht
    with pytest.raises(sa.exc.IntegrityError):
        migrated.execute(insert.bindparams(status="DRAFT"))
