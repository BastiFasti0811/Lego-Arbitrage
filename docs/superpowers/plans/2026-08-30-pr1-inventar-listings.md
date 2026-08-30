# PR 1: Inventar für alles + manueller Listing-Status — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Lego-Inventar wird zum Inventar für beliebige Artikel (item_type, Warengruppe, optionaler Kaufpreis), und pro Artikel ist über eine neue `listings`-Tabelle sichtbar und pflegbar, wo er eingestellt ist (Plattform, Preis, Datum, URL, Schmerzgrenze) — inklusive Historie, Verkauft-Checkliste und „Posten teilen".

**Architecture:** Generalisierung des bestehenden `InventoryItem` statt neuem Modul (ADR 0001). Neue Models `Listing` + `ListingPriceChange` mit partiellem Unique-Index (max. ein offenes Listing je Artikel+Plattform). Listing-Routen in neuer Datei `listings.py` (inventory.py hat 714 LOC); reine Regel-Logik in `app/services/listing_rules.py`, damit sie ohne DB testbar ist (Repo-Stil: Fake-Sessions, kein conftest). Kein Auto-Posting (ADR 0002) — alles manuell gepflegt.

**Tech Stack:** FastAPI + SQLAlchemy 2 (async) + Alembic; React 19 + react-query + Tailwind; pytest (asyncio_mode=auto), ruff, eslint.

**Spec:** `docs/superpowers/specs/2026-08-30-inventar-fuer-alles-design.md` (PR-1-Umfang; Begriffe in `CONTEXT.md`, Entscheidungen in `docs/adr/0001` + `0002`)

## Global Constraints

- Python ≥ 3.12, ruff line-length 120, Regeln E/F/I/N/W/UP — nach jedem Backend-Task `ruff check` laufen lassen.
- Datums-/Zeitwerte: **immer** `from datetime import UTC, date, datetime` und `datetime.now(UTC)` (der `datetime.UTC`-Stil war der 5-Monats-Produktionsbug). Alle neuen `DateTime`-Spalten mit `timezone=True` (`test_model_timezones.py` prüft das).
- Enums als `StrEnum` (Muster: `InventoryStatus` in `backend/app/models/inventory.py`); in der DB als `String`-Spalten, nie DB-Enums.
- Keine neuen Python-/JS-Dependencies in diesem PR.
- UI-Texte deutsch, bestehende Tailwind-Klassen (`bg-lego-yellow`, `text-go-star`, `bg-bg-card`, `border-border` …) wiederverwenden.
- **Vor jedem Commit** `git branch --show-current` prüfen — muss `feat/inventar-listings` sein (geteilter Worktree, fremde Sessions wechseln Branches).
- Commits: `git commit -m "<message>" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"`.
- Backend-Kommandos aus `backend/` mit dem venv-Python: `.venv\Scripts\python.exe -m pytest …` bzw. `.venv\Scripts\python.exe -m ruff check app tests`.
- Alle Pfade unten relativ zur Repo-Wurzel `F:\-=Projekte\Privat\Lego-Arbitrage`.

---

### Task 1: Branch + Design-Dokumente

**Files:**
- Commit (bereits im Working Tree, untracked): `CONTEXT.md`, `docs/adr/0001-ein-inventar-fuer-alle-artikelarten.md`, `docs/adr/0002-kein-automatisches-posten.md`, `docs/superpowers/specs/2026-08-30-inventar-fuer-alles-design.md`, `docs/superpowers/plans/2026-08-30-pr1-inventar-listings.md`

**Interfaces:**
- Produces: Branch `feat/inventar-listings` auf Stand `origin/main`, Design-Docs versioniert.

- [ ] **Step 1: Branch anlegen**

```bash
git fetch origin && git checkout -b feat/inventar-listings origin/main
```

Erwartung: Branch wechselt; die untracked Dateien (CONTEXT.md, docs/…) bleiben im Working Tree liegen (sie sind unversioniert und branchwechsel-sicher).

- [ ] **Step 2: Test-Setup verifizieren**

```bash
cd backend && .venv\Scripts\python.exe -m pytest tests/test_migration_dismissed_offers.py -q
```

Erwartung: PASS. Falls `No module named pytest`: `cd backend && .venv\Scripts\python.exe -m pip install -e .[dev]`.

- [ ] **Step 3: Docs committen**

```bash
git add CONTEXT.md docs/adr docs/superpowers && git commit -m "docs: Glossar, ADRs und Design/Plan fuer Inventar-Generalisierung" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Models + Migration (Listing, ListingPriceChange, InventoryItem-Erweiterung)

**Files:**
- Create: `backend/app/models/listing.py`
- Modify: `backend/app/models/inventory.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/d4e8a12f9c30_inventar_fuer_alles_pr1.py`
- Test: `backend/tests/test_migration_listings.py`

**Interfaces:**
- Produces (aus `app.models.listing`):
  - `class ListingPlatform(StrEnum)`: `KLEINANZEIGEN`, `EBAY`
  - `class ListingStatus(StrEnum)`: `DRAFT`, `ACTIVE`, `PAUSED`, `ENDED`, `SOLD`
  - `class PriceType(StrEnum)`: `VB`, `FIXED`
  - `OPEN_LISTING_STATUSES: tuple[str, ...]` = `("DRAFT", "ACTIVE", "PAUSED")`
  - `class Listing(Base)` — Spalten siehe Step 3; Relationship `item` ↔ `InventoryItem.listings`, `price_changes` (cascade delete-orphan)
  - `class ListingPriceChange(Base)` — `listing_id`, `changed_at`, `old_price`, `new_price`
- Produces (aus `app.models.inventory`):
  - `class InventoryItemType(StrEnum)`: `LEGO`, `GENERIC`
  - `PRODUCT_GROUP_SUGGESTIONS: list[str]` = `["Lego", "Elektronik", "Kleidung", "Haushalt", "Spielzeug", "Diverses"]`
  - `LEGO_PRODUCT_GROUP = "Lego"`
  - `InventoryItem` neu: `item_type` (String(20), NOT NULL, server_default `"LEGO"`), `product_group` (String(100), NOT NULL, server_default `"Lego"`), `search_query` (String(300), nullable), `set_number` **nullable**, `buy_price` **nullable**, Relationship `listings` (lazy="selectin", cascade all+delete-orphan, order_by `Listing.created_at`)

- [ ] **Step 1: Migrationstest schreiben (failing)**

`backend/tests/test_migration_listings.py` — Muster ist `test_migration_dismissed_offers.py` (SQLite in-memory, Migration via importlib laden, `module.op = Operations(context)`). Zusätzlich hier: Der ALTER-/Backfill-Teil braucht die Alt-Tabellen, also legt die Fixture Mini-Versionen von `inventory_items` und `app_settings` an, bevor `upgrade()` läuft. `op.batch_alter_table` reflektiert die real vorhandene Tabelle — die Mini-Variante mit den migrationsrelevanten Spalten genügt.

```python
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
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

```bash
cd backend && .venv\Scripts\python.exe -m pytest tests/test_migration_listings.py -q
```

Erwartung: FAIL (`ModuleNotFoundError: app.models.listing`).

- [ ] **Step 3: Model `backend/app/models/listing.py` schreiben**

```python
"""Eigene Anzeigen (Listings) je Artikel und Plattform — siehe CONTEXT.md.

Nicht verwechseln mit `offers`: Das sind fremde Angebote der Arbitrage-
Pipeline. Ein Listing ist unsere eigene Anzeige, manuell eingestellt
(ADR 0002 — das System postet nie selbst).
"""

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ListingPlatform(StrEnum):
    KLEINANZEIGEN = "KLEINANZEIGEN"
    EBAY = "EBAY"


class ListingStatus(StrEnum):
    DRAFT = "DRAFT"  # Text generiert, noch nicht eingestellt (ab PR 2 in Benutzung)
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"
    SOLD = "SOLD"


class PriceType(StrEnum):
    VB = "VB"
    FIXED = "FIXED"


OPEN_LISTING_STATUSES: tuple[str, ...] = (
    ListingStatus.DRAFT.value,
    ListingStatus.ACTIVE.value,
    ListingStatus.PAUSED.value,
)

_OPEN_STATUS_SQL = text("status IN ('DRAFT','ACTIVE','PAUSED')")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        Index("ix_listings_item_id", "item_id"),
        # Genau ein offenes Listing je Artikel+Plattform; beendete Zeilen
        # bleiben als Historie stehen (Kleinanzeigen-Refresh = neue Zeile).
        Index(
            "uq_listings_open_per_platform",
            "item_id",
            "platform",
            unique=True,
            sqlite_where=_OPEN_STATUS_SQL,
            postgresql_where=_OPEN_STATUS_SQL,
        ),
    )

    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    price_type: Mapped[str] = mapped_column(String(10), nullable=False)

    # Anzeigeninhalt (Texte kommen ab PR 2 aus der KI, bleiben hier editierbar)
    title: Mapped[str | None] = mapped_column(String(120))
    body: Mapped[str | None] = mapped_column(Text)
    platform_category: Mapped[str | None] = mapped_column(String(200))

    # Einstell-Daten
    listed_at: Mapped[date | None] = mapped_column(Date)
    current_price: Mapped[float | None] = mapped_column(Float)
    url: Mapped[str | None] = mapped_column(Text)

    # Anpassungsregel (Tages-Check nutzt sie ab PR 3)
    check_interval_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="14")
    price_drop_percent: Mapped[float] = mapped_column(Float, nullable=False, server_default="10")
    min_price: Mapped[float | None] = mapped_column(Float)

    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    floor_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Offener Anpassungsvorschlag (NULL = keiner)
    suggested_price: Mapped[float | None] = mapped_column(Float)
    suggestion_reason: Mapped[str | None] = mapped_column(Text)
    suggestion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    item: Mapped["InventoryItem"] = relationship(back_populates="listings")
    price_changes: Mapped[list["ListingPriceChange"]] = relationship(
        back_populates="listing",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="ListingPriceChange.changed_at",
    )

    def __repr__(self) -> str:
        return f"<Listing item={self.item_id} {self.platform} {self.status}>"


class ListingPriceChange(Base):
    __tablename__ = "listing_price_changes"

    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    old_price: Mapped[float] = mapped_column(Float, nullable=False)
    new_price: Mapped[float] = mapped_column(Float, nullable=False)

    listing: Mapped["Listing"] = relationship(back_populates="price_changes")


from app.models.inventory import InventoryItem  # noqa: E402
```

- [ ] **Step 4: `backend/app/models/inventory.py` erweitern**

Nach `class InventoryStatus` einfügen:

```python
class InventoryItemType(StrEnum):
    LEGO = "LEGO"
    GENERIC = "GENERIC"


LEGO_PRODUCT_GROUP = "Lego"

# Startliste fuer das Warengruppen-Dropdown; per Freitext erweiterbar,
# gespeicherte Werte kommen per DISTINCT dazu (siehe /product-groups).
PRODUCT_GROUP_SUGGESTIONS = [LEGO_PRODUCT_GROUP, "Elektronik", "Kleidung", "Haushalt", "Spielzeug", "Diverses"]
```

Am `InventoryItem` ändern bzw. ergänzen (Positionen: `set_number`/`buy_price` in place ändern, neue Felder direkt unter `set_number`):

```python
    # GENERIC-Artikel haben keine Set-Nummer; LEGO-Anlage erzwingt sie im Schema.
    set_number: Mapped[str | None] = mapped_column(String(20))
    item_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default=InventoryItemType.LEGO.value)
    product_group: Mapped[str] = mapped_column(String(100), nullable=False, server_default=LEGO_PRODUCT_GROUP)
    # eBay-Suchbegriff der Preisrecherche; bei LEGO automatisch "LEGO {set_number}".
    search_query: Mapped[str | None] = mapped_column(String(300))
```

```python
    # Dachbodenfunde haben keinen Kaufpreis; Rechnungen ueberspringen sie dann.
    buy_price: Mapped[float | None] = mapped_column(Float)
```

Und unter der `photos`-Relationship:

```python
    listings: Mapped[list["Listing"]] = relationship(
        back_populates="item",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="Listing.created_at",
    )
```

Am Dateiende (neben dem bestehenden Photo-Import):

```python
from app.models.listing import Listing  # noqa: E402
```

`from enum import StrEnum` ist bereits importiert.

- [ ] **Step 5: `backend/app/models/__init__.py` ergänzen**

Import: `from app.models.listing import Listing, ListingPlatform, ListingPriceChange, ListingStatus, PriceType` und Import-Erweiterung `from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus`; alle sechs neuen Namen in `__all__` aufnehmen.

- [ ] **Step 6: Alembic-Head verifizieren**

```bash
cd backend && ls alembic/versions
```

Erwartung: höchste/neueste Revision ist `b8e5c30d7f14_add_dismissed_offers.py` (keine dazugekommene Datei). Falls doch eine neuere existiert: deren `revision`-ID als `down_revision` in Step 7 verwenden.

- [ ] **Step 7: Migration `backend/alembic/versions/d4e8a12f9c30_inventar_fuer_alles_pr1.py` schreiben**

```python
"""Inventar fuer alles (PR 1): item_type/product_group/search_query,
set_number+buy_price nullable, listings + listing_price_changes,
Cleanup der nie gelesenen Verkaufs-Credentials (ADR 0002).

Backfill: Bestand ist ausnahmslos Lego — item_type/product_group kommen
per server_default, search_query wird aus der Set-Nummer erzeugt.

Revision ID: d4e8a12f9c30
Revises: b8e5c30d7f14
Create Date: 2026-08-30 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e8a12f9c30"
down_revision: str | None = "b8e5c30d7f14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN_STATUS_SQL = sa.text("status IN ('DRAFT','ACTIVE','PAUSED')")


def upgrade() -> None:
    # --- inventory_items generalisieren (batch: laeuft auf SQLite und Postgres)
    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(sa.Column("item_type", sa.String(length=20), nullable=False, server_default="LEGO"))
        batch.add_column(sa.Column("product_group", sa.String(length=100), nullable=False, server_default="Lego"))
        batch.add_column(sa.Column("search_query", sa.String(length=300), nullable=True))
        batch.alter_column("set_number", existing_type=sa.String(length=20), nullable=True)
        batch.alter_column("buy_price", existing_type=sa.Float(), nullable=False)  # wird gleich nullable
    with op.batch_alter_table("inventory_items") as batch:
        batch.alter_column("buy_price", existing_type=sa.Float(), nullable=True)

    op.execute("UPDATE inventory_items SET search_query = 'LEGO ' || set_number WHERE set_number IS NOT NULL")

    # --- listings
    op.create_table(
        "listings",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("price_type", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("platform_category", sa.String(length=200), nullable=True),
        sa.Column("listed_at", sa.Date(), nullable=True),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("check_interval_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("price_drop_percent", sa.Float(), nullable=False, server_default="10"),
        sa.Column("min_price", sa.Float(), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("floor_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suggested_price", sa.Float(), nullable=True),
        sa.Column("suggestion_reason", sa.Text(), nullable=True),
        sa.Column("suggestion_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["item_id"], ["inventory_items.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_listings_item_id", "listings", ["item_id"])
    op.create_index(
        "uq_listings_open_per_platform",
        "listings",
        ["item_id", "platform"],
        unique=True,
        sqlite_where=_OPEN_STATUS_SQL,
        postgresql_where=_OPEN_STATUS_SQL,
    )

    # --- listing_price_changes
    op.create_table(
        "listing_price_changes",
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("old_price", sa.Float(), nullable=False),
        sa.Column("new_price", sa.Float(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_listing_price_changes_listing_id", "listing_price_changes", ["listing_id"])

    # --- nie gelesene Verkaufs-Credentials entfernen (ADR 0002)
    op.execute(
        "DELETE FROM app_settings WHERE key IN "
        "('ebay_api_key','ebay_api_secret','kleinanzeigen_email','kleinanzeigen_password')"
    )


def downgrade() -> None:
    op.drop_index("ix_listing_price_changes_listing_id", table_name="listing_price_changes")
    op.drop_table("listing_price_changes")
    op.drop_index("uq_listings_open_per_platform", table_name="listings")
    op.drop_index("ix_listings_item_id", table_name="listings")
    op.drop_table("listings")
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_column("search_query")
        batch.drop_column("product_group")
        batch.drop_column("item_type")
        batch.alter_column("buy_price", existing_type=sa.Float(), nullable=False)
        batch.alter_column("set_number", existing_type=sa.String(length=20), nullable=False)
```

Hinweis für den Umsetzer: Der doppelte `batch_alter_table`-Block für `buy_price` ist Absicht als Startpunkt — wenn der Test zeigt, dass ein einzelner Block mit direktem `nullable=True` funktioniert, den ersten `alter_column("buy_price", …)` entfernen und beide Änderungen in einem Block lassen. Maßgeblich ist der grüne Test aus Step 1.

- [ ] **Step 8: Tests laufen lassen**

```bash
cd backend && .venv\Scripts\python.exe -m pytest tests/test_migration_listings.py tests/test_model_timezones.py -q
```

Erwartung: PASS (beide Dateien — timezones prüft die neuen DateTime-Spalten mit).

- [ ] **Step 9: Gesamtsuite + Lint**

```bash
cd backend && .venv\Scripts\python.exe -m pytest -q && .venv\Scripts\python.exe -m ruff check app tests
```

Erwartung: PASS / keine Findings. (Bestehende Tests dürfen nicht brechen — `InventoryAdd` verlangt noch `buy_price: float`, das ändert erst Task 5.)

- [ ] **Step 10: Commit**

```bash
git add backend/app/models backend/alembic/versions/d4e8a12f9c30_inventar_fuer_alles_pr1.py backend/tests/test_migration_listings.py && git commit -m "feat(inventory): Datenmodell fuer Artikel aller Art und eigene Listings" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Regel-Logik `listing_rules.py`

**Files:**
- Create: `backend/app/services/listing_rules.py`
- Test: `backend/tests/test_listing_rules.py`

**Interfaces:**
- Produces (alle pure, ohne DB — genau diese Signaturen nutzen Task 6/7):
  - `default_min_price(price: float) -> float` — 70 % vom Startpreis, kaufmännisch auf 2 Stellen
  - `default_price_type(platform: str) -> str` — `"VB"` für KLEINANZEIGEN, `"FIXED"` für EBAY
  - `compute_next_check(listed_at: date, interval_days: int) -> datetime` — UTC-Mitternacht von `listed_at` + Intervall
  - `validate_activation(platform: str, price: float | None, min_price: float | None) -> str | None` — Fehlertext oder None
  - `apply_price_change(listing, new_price: float, now: datetime) -> "ListingPriceChange | None"` — setzt `current_price`, plant `next_check_at = now + interval` neu, liefert das Change-Objekt (None, wenn Preis unverändert)

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_listing_rules.py`:

```python
from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.services.listing_rules import (
    apply_price_change,
    compute_next_check,
    default_min_price,
    default_price_type,
    validate_activation,
)


def test_default_min_price_is_70_percent():
    assert default_min_price(50.0) == 35.0
    assert default_min_price(39.99) == 27.99


def test_default_price_type_by_platform():
    assert default_price_type("KLEINANZEIGEN") == "VB"
    assert default_price_type("EBAY") == "FIXED"


def test_compute_next_check_counts_from_listed_at():
    result = compute_next_check(date(2026, 8, 1), 14)
    assert result == datetime(2026, 8, 15, tzinfo=UTC)


def test_activation_requires_positive_price_and_min_price():
    assert validate_activation("KLEINANZEIGEN", None, 10.0) is not None
    assert validate_activation("KLEINANZEIGEN", 0, 10.0) is not None
    assert validate_activation("KLEINANZEIGEN", 50.0, None) is not None
    assert validate_activation("KLEINANZEIGEN", 50.0, 60.0) is not None  # Schmerzgrenze ueber Startpreis
    assert validate_activation("UNBEKANNT", 50.0, 35.0) is not None
    assert validate_activation("KLEINANZEIGEN", 50.0, 35.0) is None


def _listing(price=50.0, interval=14):
    return SimpleNamespace(
        id=1, current_price=price, check_interval_days=interval, next_check_at=None
    )


def test_apply_price_change_records_old_and_new_and_reschedules():
    listing = _listing()
    now = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)

    change = apply_price_change(listing, 45.0, now)

    assert change.old_price == 50.0
    assert change.new_price == 45.0
    assert change.changed_at == now
    assert listing.current_price == 45.0
    assert listing.next_check_at == datetime(2026, 9, 13, 8, 0, tzinfo=UTC)


def test_apply_price_change_ignores_unchanged_price():
    listing = _listing()
    assert apply_price_change(listing, 50.0, datetime(2026, 8, 30, tzinfo=UTC)) is None
    assert listing.next_check_at is None
```

- [ ] **Step 2: Laufen lassen — FAIL** (`ModuleNotFoundError: app.services.listing_rules`)

```bash
cd backend && .venv\Scripts\python.exe -m pytest tests/test_listing_rules.py -q
```

- [ ] **Step 3: Implementieren**

`backend/app/services/listing_rules.py`:

```python
"""Reine Regel-Logik fuer Listings — ohne DB, damit direkt testbar.

Preisvorschlags-Rundung auf glatte Betraege (unter 50 auf 1, darueber
auf 5) lebt erst im Tages-Check (PR 3); hier stehen die Regeln, die der
manuelle Lifecycle aus PR 1 braucht.
"""

from datetime import UTC, date, datetime, timedelta

from app.models.listing import ListingPlatform, ListingPriceChange, PriceType


def default_min_price(price: float) -> float:
    """Schmerzgrenzen-Vorbefuellung: 70 % vom Startpreis (Grill-Entscheid Q5)."""
    return round(price * 0.7, 2)


def default_price_type(platform: str) -> str:
    return PriceType.VB.value if platform == ListingPlatform.KLEINANZEIGEN.value else PriceType.FIXED.value


def compute_next_check(listed_at: date, interval_days: int) -> datetime:
    """Faelligkeit zaehlt ab dem Einstell-Datum, auch bei nachtraeglicher Erfassung."""
    return datetime(listed_at.year, listed_at.month, listed_at.day, tzinfo=UTC) + timedelta(days=interval_days)


def validate_activation(platform: str, price: float | None, min_price: float | None) -> str | None:
    if platform not in (p.value for p in ListingPlatform):
        return f"Unbekannte Plattform: {platform}"
    if not price or price <= 0:
        return "Preis muss groesser 0 sein"
    if min_price is None or min_price <= 0:
        return "Schmerzgrenze (min_price) ist Pflicht"
    if min_price > price:
        return "Schmerzgrenze liegt ueber dem Startpreis"
    return None


def apply_price_change(listing, new_price: float, now: datetime) -> ListingPriceChange | None:
    """Preisaenderung = Anpassung passiert: Event schreiben, Check neu planen."""
    if listing.current_price == new_price:
        return None
    change = ListingPriceChange(
        listing_id=listing.id,
        changed_at=now,
        old_price=listing.current_price,
        new_price=new_price,
    )
    listing.current_price = new_price
    listing.next_check_at = now + timedelta(days=listing.check_interval_days)
    return change
```

- [ ] **Step 4: Laufen lassen — PASS**, dann `ruff check app tests`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/listing_rules.py backend/tests/test_listing_rules.py && git commit -m "feat(listings): Regel-Logik fuer Aktivierung, Schmerzgrenze und Preisaenderung" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Rechenpfade vertragen fehlenden Kaufpreis

**Files:**
- Modify: `backend/app/api/routes/inventory.py` (`_to_response`, `_recalculate_unrealized_metrics`, `mark_as_sold`, `portfolio_summary`)
- Test: `backend/tests/test_inventory_optional_buy_price.py`

**Interfaces:**
- Consumes: bestehende Helpers aus `inventory.py`
- Produces: `InventoryResponse.buy_price: float | None`, `InventoryResponse.total_invested: float | None` (None statt erfundener 0-Werte); `mark_as_sold` setzt bei fehlendem Kaufpreis `realized_profit=None`/`realized_roi_percent=None`; `portfolio_summary` zählt Artikel ohne Kaufpreis nicht in `total_invested`/`unrealized`, wohl aber in `current_value` (nur Marktpreis) und Stückzahlen.

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_inventory_optional_buy_price.py` (Stil: `test_inventory_market_snapshot.py` — SimpleNamespace, direkte Helper-Aufrufe):

```python
"""Dachbodenfunde haben keinen Kaufpreis (Spec PR 1, Grill-Entscheid Q4):
ehrlich fehlend schlaegt falsch berechnet — nirgends 0 als Ersatzwert."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.api.routes.inventory import _recalculate_unrealized_metrics, _to_response


def _item(**overrides):
    base = dict(
        id=1,
        set_number=None,
        set_name="Bohrmaschine",
        theme=None,
        image_url=None,
        item_type="GENERIC",
        product_group="Elektronik",
        search_query="Bosch PSB 500",
        buy_price=None,
        buy_shipping=0.0,
        buy_date=date(2026, 8, 1),
        buy_platform=None,
        buy_url=None,
        condition="USED_COMPLETE",
        quantity=1,
        notes=None,
        photos=[],
        listings=[],
        current_market_price=None,
        market_price_updated_at=None,
        unrealized_profit=None,
        unrealized_roi_percent=None,
        sell_signal_active=False,
        sell_signal_reason=None,
        status="HOLDING",
        sell_price=None,
        sell_date=None,
        sell_platform=None,
        realized_profit=None,
        realized_roi_percent=None,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_response_has_no_invented_invest_without_buy_price():
    response = _to_response(_item())
    assert response.buy_price is None
    assert response.total_invested is None


def test_response_still_computes_invest_with_buy_price():
    response = _to_response(_item(buy_price=100.0, buy_shipping=5.0))
    assert response.total_invested == 105.0


def test_unrealized_metrics_skip_items_without_buy_price():
    item = _item(current_market_price=80.0)
    _recalculate_unrealized_metrics(item)
    assert item.unrealized_profit is None
    assert item.unrealized_roi_percent is None
```

- [ ] **Step 2: Laufen lassen — FAIL** (Pydantic-Validation: `buy_price`/`total_invested` erwarten float; `unrealized_profit` wäre 80.0)

```bash
cd backend && .venv\Scripts\python.exe -m pytest tests/test_inventory_optional_buy_price.py -q
```

- [ ] **Step 3: Implementieren**

In `backend/app/api/routes/inventory.py`:

1. `InventoryResponse`: `set_number: str | None`, `buy_price: float | None`, `total_invested: float | None`, neue Felder `item_type: str`, `product_group: str`, `search_query: str | None` (die drei füllt Task 5/6 vollständig — hier schon deklarieren, `_to_response` reicht sie durch).
2. `_to_response`: 

```python
    has_buy_price = item.buy_price is not None
    total_invested = round(item.buy_price + (item.buy_shipping or 0), 2) if has_buy_price else None
```

und in den Konstruktor `buy_price=item.buy_price`, `total_invested=total_invested`, `item_type=item.item_type`, `product_group=item.product_group`, `search_query=item.search_query`.
3. `_recalculate_unrealized_metrics`: früher Ausstieg ergänzen:

```python
    if item.current_market_price is None or item.buy_price is None:
        item.unrealized_profit = None
        item.unrealized_roi_percent = None
        return
```

4. `mark_as_sold`: die Gewinnrechnung absichern:

```python
    if item.buy_price is not None:
        total_invested = item.buy_price + (item.buy_shipping or 0)
        selling_costs = sum(calculate_ebay_fees(data.sell_price))
        realized_profit = data.sell_price - total_invested - selling_costs
        item.realized_profit = round(realized_profit, 2)
        item.realized_roi_percent = (
            round((realized_profit / total_invested) * 100, 1) if total_invested > 0 else 0
        )
    else:
        item.realized_profit = None
        item.realized_roi_percent = None
```

Der Feedback-Block (`_ensure_feedback_set`/`DealFeedback`) läuft nur im `buy_price is not None`-Zweig weiter (er rechnet mit `purchase_price`).
5. `portfolio_summary`:

```python
    priced_holding = [i for i in holding if i.buy_price is not None]
    total_invested = sum(i.buy_price + (i.buy_shipping or 0) for i in priced_holding)
    current_value = sum(
        i.current_market_price
        if i.current_market_price is not None
        else (i.buy_price + (i.buy_shipping or 0)) if i.buy_price is not None else 0
        for i in holding
    )
    unrealized = current_value - total_invested - sum(
        i.current_market_price or 0 for i in holding if i.buy_price is None
    )
```

(Artikel ohne Kaufpreis erhöhen `current_value`, fließen aber nicht in `unrealized` — sonst zählte ihr voller Marktwert als „Gewinn".)

- [ ] **Step 4: Alle Inventar-Tests laufen lassen — PASS**

```bash
cd backend && .venv\Scripts\python.exe -m pytest tests/test_inventory_optional_buy_price.py tests/test_inventory_market_snapshot.py -q
```

- [ ] **Step 5: Gesamtsuite + Lint, dann Commit**

```bash
cd backend && .venv\Scripts\python.exe -m pytest -q && .venv\Scripts\python.exe -m ruff check app tests
```

```bash
git add backend/app/api/routes/inventory.py backend/tests/test_inventory_optional_buy_price.py && git commit -m "feat(inventory): Kaufpreis optional - Rechnungen ueberspringen statt erfinden" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Anlage-Validierung LEGO/GENERIC + Warengruppen-Endpoint + Filter

**Files:**
- Modify: `backend/app/api/routes/inventory.py` (`InventoryAdd`, `InventoryUpdate`, `add_inventory_item`, `list_inventory`, neuer Endpoint `/product-groups`)
- Test: `backend/tests/test_inventory_generic_validation.py`

**Interfaces:**
- Consumes: `InventoryItemType`, `LEGO_PRODUCT_GROUP`, `PRODUCT_GROUP_SUGGESTIONS` aus `app.models.inventory`
- Produces:
  - `InventoryAdd` neu: `item_type: str = "LEGO"`, `set_number: str | None = None`, `buy_price: float | None = None`, `product_group: str | None = None`, `search_query: str | None = None` + `model_validator` (Regeln unten)
  - `InventoryUpdate` neu: `product_group: str | None = None`, `search_query: str | None = None`
  - `GET /api/inventory/product-groups` → `list[str]` (Startliste ∪ DISTINCT aus DB, sortiert)
  - `GET /api/inventory/?item_type=…&product_group=…` filtert

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_inventory_generic_validation.py`:

```python
from datetime import date

import pytest
from pydantic import ValidationError

from app.api.routes.inventory import InventoryAdd


def _payload(**overrides):
    base = dict(set_name="Testartikel", buy_date=date(2026, 8, 30))
    base.update(overrides)
    return base


def test_lego_requires_set_number():
    with pytest.raises(ValidationError, match="Set-Nummer"):
        InventoryAdd(**_payload(item_type="LEGO", buy_price=10.0))


def test_lego_forces_group_and_derives_search_query():
    item = InventoryAdd(**_payload(item_type="LEGO", set_number="75331", buy_price=10.0, product_group="Elektronik"))
    assert item.product_group == "Lego"
    assert item.search_query == "LEGO 75331"


def test_lego_keeps_explicit_search_query():
    item = InventoryAdd(**_payload(item_type="LEGO", set_number="75331", buy_price=10.0, search_query="LEGO Razor Crest 75331"))
    assert item.search_query == "LEGO Razor Crest 75331"


def test_generic_needs_no_set_number_and_no_buy_price():
    item = InventoryAdd(**_payload(item_type="GENERIC"))
    assert item.set_number is None
    assert item.buy_price is None
    assert item.product_group == "Diverses"


def test_generic_ignores_set_number():
    item = InventoryAdd(**_payload(item_type="GENERIC", set_number="75331"))
    assert item.set_number is None


def test_unknown_item_type_is_rejected():
    with pytest.raises(ValidationError):
        InventoryAdd(**_payload(item_type="OBST"))
```

- [ ] **Step 2: Laufen lassen — FAIL**

```bash
cd backend && .venv\Scripts\python.exe -m pytest tests/test_inventory_generic_validation.py -q
```

- [ ] **Step 3: Implementieren**

In `inventory.py` — Import ergänzen: `from pydantic import BaseModel, model_validator` und `from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus, LEGO_PRODUCT_GROUP, PRODUCT_GROUP_SUGGESTIONS`.

`InventoryAdd` umbauen:

```python
class InventoryAdd(BaseModel):
    item_type: str = InventoryItemType.LEGO.value
    set_number: str | None = None
    set_name: str
    product_group: str | None = None
    search_query: str | None = None
    theme: str | None = None
    image_url: str | None = None
    buy_price: float | None = None
    buy_shipping: float = 0.0
    buy_date: date
    buy_platform: str | None = None
    buy_url: str | None = None
    condition: str = "NEW_SEALED"
    quantity: int = 1
    notes: str | None = None

    @model_validator(mode="after")
    def _apply_type_rules(self):
        if self.item_type not in (t.value for t in InventoryItemType):
            raise ValueError(f"Unbekannter item_type: {self.item_type}")
        if self.item_type == InventoryItemType.LEGO.value:
            if not (self.set_number or "").strip():
                raise ValueError("Set-Nummer ist bei Lego-Artikeln Pflicht")
            self.product_group = LEGO_PRODUCT_GROUP
            if not self.search_query:
                self.search_query = f"LEGO {self.set_number}"
        else:
            self.set_number = None
            self.product_group = (self.product_group or "").strip() or "Diverses"
        return self
```

`add_inventory_item`: die drei neuen Felder in den Konstruktor (`item_type=data.item_type, product_group=data.product_group, search_query=data.search_query`); der `_hydrate_market_snapshot`-Aufruf nur noch für LEGO-Artikel (`if data.item_type == InventoryItemType.LEGO.value:` — GENERIC hat keine `LegoSet`-Zeile). Achtung: `_hydrate_market_snapshot` und `_find_matching_analysis` filtern auf `set_number` — bei `None` liefern die Queries nichts; zusätzlich in `_hydrate_market_snapshot` als erste Zeile absichern: `if item.set_number is None: return False`.

`InventoryUpdate`: Felder `product_group: str | None = None` und `search_query: str | None = None` ergänzen (kein `item_type`-Wechsel nach Anlage — bewusst).

Neuer Endpoint (VOR den `/{item_id}`-Routen platzieren, wie `/platforms`):

```python
@router.get("/product-groups")
async def list_product_groups(session: AsyncSession = Depends(get_session)):
    """Warengruppen fuers Dropdown: Startliste plus alles bereits Vergebene."""
    result = await session.execute(
        select(InventoryItem.product_group).where(InventoryItem.product_group.is_not(None)).distinct()
    )
    stored = {row[0] for row in result.all() if row[0]}
    return sorted(stored | set(PRODUCT_GROUP_SUGGESTIONS))
```

`list_inventory`: Parameter `item_type: str | None = Query(default=None)` und `product_group: str | None = Query(default=None)`, je `query = query.where(...)` wenn gesetzt.

- [ ] **Step 4: Laufen lassen — PASS**, dann Gesamtsuite + Lint

```bash
cd backend && .venv\Scripts\python.exe -m pytest -q && .venv\Scripts\python.exe -m ruff check app tests
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/inventory.py backend/tests/test_inventory_generic_validation.py && git commit -m "feat(inventory): GENERIC-Artikel anlegen - Typregeln, Warengruppen, Filter" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Listing-Routen + Einbettung in InventoryResponse

**Files:**
- Create: `backend/app/api/routes/listings.py`
- Modify: `backend/app/main.py` (Router registrieren)
- Modify: `backend/app/api/routes/inventory.py` (`InventoryResponse.listings`, `_to_response`)

**Interfaces:**
- Consumes: `listing_rules` (Task 3), Models (Task 2)
- Produces (Task 7/10/11 verlassen sich exakt hierauf):
  - `class ListingResponse(BaseModel)`: `id, platform, status, price_type, title|None, body|None, platform_category|None, listed_at|None, current_price|None, url|None, min_price|None, check_interval_days, price_drop_percent, next_check_at|None, suggested_price|None, suggestion_reason|None, suggestion_at|None, at_floor: bool, price_changes: list[PriceChangeResponse]`
  - `def to_listing_response(listing) -> ListingResponse` (öffentlich, `at_floor = status==ACTIVE and min_price is not None and current_price is not None and current_price <= min_price`)
  - `def open_listing_responses(item) -> list[ListingResponse]` — nur Status in `OPEN_LISTING_STATUSES`
  - Routen (Prefix `/api/inventory`): `GET /{item_id}/listings` (alle, neueste zuerst), `POST /{item_id}/listings` (Body `ListingCreate`, legt ACTIVE an), `PATCH /{item_id}/listings/{listing_id}` (Body `ListingUpdate`), `POST /{item_id}/listings/{listing_id}/end`, `DELETE /{item_id}/listings/{listing_id}`
  - `InventoryResponse.listings: list[ListingResponse]` (nur offene — für Karte/Badges)

- [ ] **Step 1: `backend/app/api/routes/listings.py` schreiben**

```python
"""Listing-Lifecycle: manuell gepflegte eigene Anzeigen (ADR 0002).

Eigene Router-Datei, weil inventory.py bereits >700 Zeilen traegt.
Kein Import aus inventory.py — sonst Zirkularimport, denn inventory.py
bettet ListingResponse in seine InventoryResponse ein.
"""

from datetime import UTC, date, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.models.inventory import InventoryItem
from app.models.listing import OPEN_LISTING_STATUSES, Listing, ListingStatus
from app.services.listing_rules import (
    apply_price_change,
    compute_next_check,
    default_min_price,
    default_price_type,
    validate_activation,
)

logger = structlog.get_logger()
router = APIRouter()


class ListingCreate(BaseModel):
    platform: str
    current_price: float
    listed_at: date | None = None
    url: str | None = None
    min_price: float | None = None
    price_type: str | None = None
    check_interval_days: int = 14
    price_drop_percent: float = 10.0


class ListingUpdate(BaseModel):
    current_price: float | None = None
    url: str | None = None
    min_price: float | None = None
    status: str | None = None  # nur ACTIVE <-> PAUSED
    price_type: str | None = None
    check_interval_days: int | None = None
    price_drop_percent: float | None = None
    title: str | None = None
    body: str | None = None


class PriceChangeResponse(BaseModel):
    id: int
    changed_at: datetime
    old_price: float
    new_price: float

    model_config = {"from_attributes": True}


class ListingResponse(BaseModel):
    id: int
    platform: str
    status: str
    price_type: str
    title: str | None
    body: str | None
    platform_category: str | None
    listed_at: date | None
    current_price: float | None
    url: str | None
    min_price: float | None
    check_interval_days: int
    price_drop_percent: float
    next_check_at: datetime | None
    suggested_price: float | None
    suggestion_reason: str | None
    suggestion_at: datetime | None
    at_floor: bool
    price_changes: list[PriceChangeResponse]
    created_at: datetime

    model_config = {"from_attributes": True}


def to_listing_response(listing: Listing) -> ListingResponse:
    at_floor = (
        listing.status == ListingStatus.ACTIVE.value
        and listing.min_price is not None
        and listing.current_price is not None
        and listing.current_price <= listing.min_price
    )
    return ListingResponse(
        id=listing.id,
        platform=listing.platform,
        status=listing.status,
        price_type=listing.price_type,
        title=listing.title,
        body=listing.body,
        platform_category=listing.platform_category,
        listed_at=listing.listed_at,
        current_price=listing.current_price,
        url=listing.url,
        min_price=listing.min_price,
        check_interval_days=listing.check_interval_days,
        price_drop_percent=listing.price_drop_percent,
        next_check_at=listing.next_check_at,
        suggested_price=listing.suggested_price,
        suggestion_reason=listing.suggestion_reason,
        suggestion_at=listing.suggestion_at,
        at_floor=at_floor,
        price_changes=[PriceChangeResponse.model_validate(change) for change in listing.price_changes],
        created_at=listing.created_at,
    )


def open_listing_responses(item: InventoryItem) -> list[ListingResponse]:
    return [to_listing_response(x) for x in item.listings if x.status in OPEN_LISTING_STATUSES]


async def _get_item(item_id: int, session: AsyncSession) -> InventoryItem:
    result = await session.execute(select(InventoryItem).where(InventoryItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"Inventory item {item_id} not found")
    return item


async def _get_listing(item_id: int, listing_id: int, session: AsyncSession) -> Listing:
    result = await session.execute(
        select(Listing).where(Listing.id == listing_id, Listing.item_id == item_id)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing nicht gefunden")
    return listing


@router.get("/{item_id}/listings", response_model=list[ListingResponse])
async def list_listings(item_id: int, session: AsyncSession = Depends(get_session)):
    """Alle Listings inkl. Historie, neueste zuerst."""
    item = await _get_item(item_id, session)
    ordered = sorted(item.listings, key=lambda x: x.created_at, reverse=True)
    return [to_listing_response(x) for x in ordered]


@router.post("/{item_id}/listings", response_model=ListingResponse)
async def create_listing(item_id: int, data: ListingCreate, session: AsyncSession = Depends(get_session)):
    """Als eingestellt markieren: Mensch hat die Anzeige angelegt, wir merken sie."""
    item = await _get_item(item_id, session)
    platform = data.platform.strip().upper()
    min_price = data.min_price if data.min_price is not None else default_min_price(data.current_price)
    error = validate_activation(platform, data.current_price, min_price)
    if error:
        raise HTTPException(status_code=400, detail=error)
    if any(x.platform == platform and x.status in OPEN_LISTING_STATUSES for x in item.listings):
        raise HTTPException(status_code=400, detail=f"Es gibt schon ein offenes Listing auf {platform}")

    listed_at = data.listed_at or date.today()
    listing = Listing(
        item_id=item.id,
        platform=platform,
        status=ListingStatus.ACTIVE.value,
        price_type=(data.price_type or default_price_type(platform)),
        listed_at=listed_at,
        current_price=data.current_price,
        url=(data.url or "").strip() or None,
        min_price=min_price,
        check_interval_days=data.check_interval_days,
        price_drop_percent=data.price_drop_percent,
        next_check_at=compute_next_check(listed_at, data.check_interval_days),
    )
    session.add(listing)
    await session.commit()
    await session.refresh(listing)
    logger.info("listing.activated", item_id=item.id, platform=platform, price=data.current_price)
    return to_listing_response(listing)


@router.patch("/{item_id}/listings/{listing_id}", response_model=ListingResponse)
async def update_listing(
    item_id: int, listing_id: int, data: ListingUpdate, session: AsyncSession = Depends(get_session)
):
    listing = await _get_listing(item_id, listing_id, session)
    if listing.status not in OPEN_LISTING_STATUSES:
        raise HTTPException(status_code=400, detail="Beendete Listings sind Historie und unveraenderlich")

    if data.status is not None:
        allowed = {ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value}
        if data.status not in allowed or listing.status not in allowed:
            raise HTTPException(status_code=400, detail="Nur Wechsel zwischen ACTIVE und PAUSED erlaubt")
        listing.status = data.status

    for field in ("url", "min_price", "price_type", "check_interval_days", "price_drop_percent", "title", "body"):
        value = getattr(data, field)
        if value is not None:
            setattr(listing, field, value)

    if data.current_price is not None:
        change = apply_price_change(listing, data.current_price, datetime.now(UTC))
        if change is not None:
            session.add(change)

    await session.commit()
    await session.refresh(listing)
    return to_listing_response(listing)


@router.post("/{item_id}/listings/{listing_id}/end", response_model=ListingResponse)
async def end_listing(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    """Anzeige geloescht/abgelaufen — Zeile bleibt als Historie (ENDED)."""
    listing = await _get_listing(item_id, listing_id, session)
    if listing.status not in OPEN_LISTING_STATUSES:
        raise HTTPException(status_code=400, detail="Listing ist bereits beendet")
    listing.status = ListingStatus.ENDED.value
    await session.commit()
    await session.refresh(listing)
    return to_listing_response(listing)


@router.delete("/{item_id}/listings/{listing_id}")
async def delete_listing(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    """Fuer Fehleingaben — loescht die Zeile samt Preis-Historie endgueltig."""
    listing = await _get_listing(item_id, listing_id, session)
    await session.delete(listing)
    await session.commit()
    return {"status": "deleted", "id": listing_id}
```

- [ ] **Step 2: Router registrieren**

`backend/app/main.py`: Import um `listings` erweitern (dieselbe Import-Zeile wie die anderen Routen-Module) und direkt unter der Inventory-Zeile:

```python
app.include_router(listings.router, prefix="/api/inventory", tags=["Listings"])
```

- [ ] **Step 3: `InventoryResponse` um Listings ergänzen**

`inventory.py`: Import `from app.api.routes.listings import ListingResponse, open_listing_responses`; Feld `listings: list[ListingResponse] = []` in `InventoryResponse`; in `_to_response` den Konstruktor um `listings=open_listing_responses(item)` erweitern. In `test_inventory_optional_buy_price.py` liefert das leere `listings=[]`-Attribut des SimpleNamespace bereits eine leere Liste.

- [ ] **Step 4: Verifizieren — App importierbar, Suite grün, Lint**

```bash
cd backend && .venv\Scripts\python.exe -c "from app.main import app; print([r.path for r in app.routes if 'listings' in r.path])" && .venv\Scripts\python.exe -m pytest -q && .venv\Scripts\python.exe -m ruff check app tests
```

Erwartung: die fünf Listing-Pfade werden gedruckt; Suite PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/listings.py backend/app/api/routes/inventory.py backend/app/main.py && git commit -m "feat(listings): Lifecycle-Endpoints und Einbettung ins Inventar" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Verkauft-Flow schließt das verkaufende Listing

**Files:**
- Modify: `backend/app/api/routes/inventory.py` (`mark_as_sold`)
- Test: `backend/tests/test_mark_sold_listings.py`

**Interfaces:**
- Consumes: `OPEN_LISTING_STATUSES`, `ListingStatus` aus `app.models.listing`
- Produces: `close_sold_listing(item, sell_platform) -> list` — Modul-Funktion in `inventory.py`: setzt das offene Listing der Verkaufsplattform auf SOLD, liefert die übrigen offenen Listings (Checkliste). `mark_as_sold` ruft sie auf; die Response enthält die verbleibenden offenen Listings ohnehin über `InventoryResponse.listings`.

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_mark_sold_listings.py`:

```python
"""Verkauft auf A, aktiv auf B: die Leiche darf nicht vergessen werden
(Spec Verkauft-Flow, Grill-Entscheid Q2)."""

from types import SimpleNamespace

from app.api.routes.inventory import close_sold_listing


def _listing(platform, status):
    return SimpleNamespace(platform=platform, status=status)


def test_sold_platform_listing_is_closed_and_others_reported():
    ebay = _listing("EBAY", "ACTIVE")
    ka = _listing("KLEINANZEIGEN", "ACTIVE")
    item = SimpleNamespace(listings=[ebay, ka])

    remaining = close_sold_listing(item, "eBay")

    assert ebay.status == "SOLD"
    assert remaining == [ka]


def test_direct_sale_without_listing_changes_nothing():
    ka = _listing("KLEINANZEIGEN", "ENDED")
    item = SimpleNamespace(listings=[ka])

    remaining = close_sold_listing(item, "Flohmarkt")

    assert ka.status == "ENDED"
    assert remaining == []


def test_no_platform_given_keeps_open_listings_as_checklist():
    ka = _listing("KLEINANZEIGEN", "PAUSED")
    item = SimpleNamespace(listings=[ka])

    remaining = close_sold_listing(item, None)

    assert ka.status == "PAUSED"
    assert remaining == [ka]
```

- [ ] **Step 2: Laufen lassen — FAIL** (`ImportError: close_sold_listing`)

```bash
cd backend && .venv\Scripts\python.exe -m pytest tests/test_mark_sold_listings.py -q
```

- [ ] **Step 3: Implementieren**

In `inventory.py` — Import `from app.models.listing import OPEN_LISTING_STATUSES, ListingStatus`; neue Funktion bei den anderen Helpers:

```python
def close_sold_listing(item, sell_platform: str | None) -> list:
    """Schliesst das Listing der Verkaufsplattform; Rest ist die Loesch-Checkliste."""
    platform_key = (sell_platform or "").strip().upper()
    remaining = []
    for listing in item.listings:
        if listing.status not in OPEN_LISTING_STATUSES:
            continue
        if platform_key and listing.platform == platform_key:
            listing.status = ListingStatus.SOLD.value
        else:
            remaining.append(listing)
    return remaining
```

In `mark_as_sold` nach `item.sell_platform = data.sell_platform`:

```python
    remaining_open = close_sold_listing(item, data.sell_platform)
    if remaining_open:
        logger.info(
            "inventory.sold_with_open_listings",
            item_id=item.id,
            platforms=[x.platform for x in remaining_open],
        )
```

- [ ] **Step 4: PASS + Gesamtsuite + Lint, dann Commit**

```bash
cd backend && .venv\Scripts\python.exe -m pytest -q && .venv\Scripts\python.exe -m ruff check app tests
```

```bash
git add backend/app/api/routes/inventory.py backend/tests/test_mark_sold_listings.py && git commit -m "feat(inventory): Verkauf schliesst das Plattform-Listing und meldet offene Leichen" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: „Posten teilen"

**Files:**
- Modify: `backend/app/api/routes/inventory.py` (Endpoint + Foto-Kopier-Helper)
- Test: `backend/tests/test_split_item.py`

**Interfaces:**
- Consumes: `_photo_dir`, `_get_item`, `PHOTO_STORAGE_ROOT` (bestehend)
- Produces:
  - `copy_item_photos(photos, source_dir: Path, target_dir: Path) -> list[dict]` — kopiert Dateien, liefert `[{"filename": neu, "original_filename": …, "content_type": …, "sort_order": …}]`
  - `POST /api/inventory/{item_id}/split` Body `{"split_quantity": int}` → `InventoryResponse` des neuen Artikels. Regeln: nur wenn `1 <= split_quantity < quantity`, nicht bei SOLD; Listings bleiben beim Original (sie beziehen sich auf den Posten, der weiterläuft); Fotos werden als Dateien kopiert.

- [ ] **Step 1: Failing Test für den Foto-Kopier-Helper**

`backend/tests/test_split_item.py`:

```python
"""Posten teilen kopiert Foto-DATEIEN — sonst zeigt der neue Artikel ins Leere,
sobald der alte geloescht wird (Grill-Entscheid Q17)."""

from types import SimpleNamespace

from app.api.routes.inventory import copy_item_photos


def _photo(filename, sort_order=0):
    return SimpleNamespace(
        filename=filename,
        original_filename=f"orig-{filename}",
        content_type="image/jpeg",
        sort_order=sort_order,
    )


def test_files_are_copied_with_fresh_names(tmp_path):
    source = tmp_path / "1"
    target = tmp_path / "2"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"foto-a")
    (source / "b.jpg").write_bytes(b"foto-b")

    result = copy_item_photos([_photo("a.jpg", 0), _photo("b.jpg", 1)], source, target)

    assert len(result) == 2
    assert result[0]["filename"] != "a.jpg"  # frischer uuid-Name, keine Kollision
    assert (target / result[0]["filename"]).read_bytes() == b"foto-a"
    assert result[0]["sort_order"] == 0
    assert result[1]["sort_order"] == 1
    assert result[0]["original_filename"] == "orig-a.jpg"


def test_missing_source_file_is_skipped(tmp_path):
    source = tmp_path / "1"
    target = tmp_path / "2"
    source.mkdir()

    result = copy_item_photos([_photo("fehlt.jpg")], source, target)

    assert result == []
```

- [ ] **Step 2: Laufen lassen — FAIL**, dann Helper implementieren

In `inventory.py` (Import `from shutil import copy2, rmtree` — `rmtree` ist schon da):

```python
def copy_item_photos(photos, source_dir: Path, target_dir: Path) -> list[dict]:
    """Kopiert Foto-Dateien fuer einen geteilten Posten; fehlende Dateien werden uebersprungen."""
    copied: list[dict] = []
    for photo in photos:
        source = source_dir / photo.filename
        if not source.exists():
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        new_name = f"{uuid4().hex}{Path(photo.filename).suffix}"
        copy2(source, target_dir / new_name)
        copied.append(
            {
                "filename": new_name,
                "original_filename": photo.original_filename,
                "content_type": photo.content_type,
                "sort_order": photo.sort_order,
            }
        )
    return copied
```

- [ ] **Step 3: Endpoint implementieren**

```python
class SplitRequest(BaseModel):
    split_quantity: int


@router.post("/{item_id}/split", response_model=InventoryResponse)
async def split_inventory_item(item_id: int, data: SplitRequest, session: AsyncSession = Depends(get_session)):
    """Teilt einen Posten (quantity 3 -> 2+1). Listings bleiben beim Original."""
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Verkaufte Artikel lassen sich nicht teilen")
    quantity = item.quantity or 1
    if not 1 <= data.split_quantity < quantity:
        raise HTTPException(status_code=400, detail=f"split_quantity muss zwischen 1 und {quantity - 1} liegen")

    new_item = InventoryItem(
        item_type=item.item_type,
        set_number=item.set_number,
        set_name=item.set_name,
        product_group=item.product_group,
        search_query=item.search_query,
        theme=item.theme,
        image_url=item.image_url,
        buy_price=item.buy_price,
        buy_shipping=item.buy_shipping,
        buy_date=item.buy_date,
        buy_platform=item.buy_platform,
        buy_url=item.buy_url,
        condition=item.condition,
        quantity=data.split_quantity,
        notes=item.notes,
        status=item.status,
        current_market_price=item.current_market_price,
        market_price_updated_at=item.market_price_updated_at,
    )
    session.add(new_item)
    await session.flush()

    for payload in copy_item_photos(item.photos, _photo_dir(item.id), _photo_dir(new_item.id)):
        session.add(InventoryPhoto(item_id=new_item.id, **payload))

    item.quantity = quantity - data.split_quantity
    _recalculate_unrealized_metrics(new_item)
    await session.commit()
    await session.refresh(new_item)
    logger.info("inventory.split", source=item.id, new=new_item.id, split=data.split_quantity)
    return _to_response(new_item)
```

- [ ] **Step 4: PASS + Gesamtsuite + Lint, dann Commit**

```bash
cd backend && .venv\Scripts\python.exe -m pytest tests/test_split_item.py -q && .venv\Scripts\python.exe -m pytest -q && .venv\Scripts\python.exe -m ruff check app tests
```

```bash
git add backend/app/api/routes/inventory.py backend/tests/test_split_item.py && git commit -m "feat(inventory): Posten teilen inkl. Foto-Kopien" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Settings-Cleanup (tote Verkaufs-Credentials)

**Files:**
- Modify: `backend/app/api/routes/settings.py` (`DEFAULT_SETTINGS`)
- Check/Modify: `frontend/src/pages/Settings.jsx`

**Interfaces:**
- Consumes: DB-DELETE passiert bereits in der Task-2-Migration.
- Produces: `DEFAULT_SETTINGS` ohne die vier Einträge `ebay_api_key`, `ebay_api_secret`, `kleinanzeigen_email`, `kleinanzeigen_password` (Kategorien `ebay` und `kleinanzeigen` verschwinden komplett).

- [ ] **Step 1: Die vier Dict-Einträge aus `DEFAULT_SETTINGS` löschen** (Zeilen ~58–86; Telegram/Catawiki/Whatnot/BrickLink bleiben unangetastet).

- [ ] **Step 2: Frontend prüfen**

```bash
grep -n "ebay\|kleinanzeigen" frontend/src/pages/Settings.jsx
```

Erwartung laut Architektur: Settings.jsx rendert Kategorien aus der API-Antwort — dann kein Handlungsbedarf. Falls dort Kategorie-Labels oder Icons für `ebay`/`kleinanzeigen` hartkodiert sind: diese Einträge mit entfernen.

- [ ] **Step 3: Backend-Suite + Lint (test_settings_metadata.py testet nur `describe_stored_value` — bleibt grün), dann Commit**

```bash
cd backend && .venv\Scripts\python.exe -m pytest -q && .venv\Scripts\python.exe -m ruff check app tests
```

```bash
git add backend/app/api/routes/settings.py frontend/src/pages/Settings.jsx && git commit -m "chore(settings): tote Verkaufs-Credential-Felder entfernt (ADR 0002)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Falls Settings.jsx unverändert blieb, nur die Backend-Datei adden.)

---

### Task 10: Frontend — API-Client, Badges, ListingManager

**Files:**
- Modify: `frontend/src/api/client.js`
- Create: `frontend/src/components/ListingBadges.jsx`
- Create: `frontend/src/components/ListingManager.jsx`

**Interfaces:**
- Consumes: Endpoints aus Task 5/6/8
- Produces (Task 11 bindet genau das ein):
  - `api.listListings(itemId)`, `api.createListing(itemId, data)`, `api.updateListing(itemId, listingId, data)`, `api.endListing(itemId, listingId)`, `api.deleteListing(itemId, listingId)`, `api.splitInventory(itemId, splitQuantity)`, `api.listProductGroups()`
  - `<ListingBadges item={item} />` — kompakte Badge-Zeile aus `item.listings`
  - `<ListingManager item={item} onClose={fn} onChanged={fn} />` — Modal: je Plattform offenes Listing verwalten oder „Als eingestellt markieren", darunter Historie

- [ ] **Step 1: client.js — im `// Inventory`-Block ergänzen**

```js
  listListings: (itemId) => request(`/inventory/${itemId}/listings`),
  createListing: (itemId, data) => request(`/inventory/${itemId}/listings`, { method: "POST", body: JSON.stringify(data) }),
  updateListing: (itemId, listingId, data) =>
    request(`/inventory/${itemId}/listings/${listingId}`, { method: "PATCH", body: JSON.stringify(data) }),
  endListing: (itemId, listingId) => request(`/inventory/${itemId}/listings/${listingId}/end`, { method: "POST" }),
  deleteListing: (itemId, listingId) => request(`/inventory/${itemId}/listings/${listingId}`, { method: "DELETE" }),
  splitInventory: (itemId, splitQuantity) =>
    request(`/inventory/${itemId}/split`, { method: "POST", body: JSON.stringify({ split_quantity: splitQuantity }) }),
  listProductGroups: () => request("/inventory/product-groups"),
```

- [ ] **Step 2: `ListingBadges.jsx` schreiben**

```jsx
const PLATFORM_LABELS = { KLEINANZEIGEN: "KA", EBAY: "eBay" };

function daysSince(dateString) {
  if (!dateString) return null;
  const days = Math.floor((Date.now() - new Date(dateString).getTime()) / 86400000);
  return days < 0 ? 0 : days;
}

function BadgeContent({ listing }) {
  const label = PLATFORM_LABELS[listing.platform] || listing.platform;
  if (listing.status === "PAUSED") return `${label}: pausiert`;
  const days = daysSince(listing.listed_at);
  return `${label}: aktiv${days !== null ? ` seit ${days} T` : ""}`;
}

export default function ListingBadges({ item }) {
  const open = (item.listings || []).filter((x) => x.status === "ACTIVE" || x.status === "PAUSED");
  if (open.length === 0) {
    return <span className="text-xs text-text-secondary">nicht eingestellt</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {open.map((listing) => {
        const classes = `text-xs px-2 py-0.5 rounded-full border ${
          listing.at_floor
            ? "border-no-go text-no-go"
            : listing.status === "PAUSED"
              ? "border-border text-text-secondary"
              : "border-go-star text-go-star"
        }`;
        const content = (
          <>
            <BadgeContent listing={listing} />
            {listing.current_price != null && ` · ${Math.round(listing.current_price)}\u20ac`}
            {listing.at_floor && " · Schmerzgrenze"}
          </>
        );
        return listing.url ? (
          <a key={listing.id} href={listing.url} target="_blank" rel="noreferrer" className={`${classes} hover:underline`}>
            {content} {"\u2197"}
          </a>
        ) : (
          <span key={listing.id} className={classes}>{content}</span>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 3: `ListingManager.jsx` schreiben**

Modal im Stil der bestehenden Inventar-Modals (fixed Overlay, `bg-bg-card`-Panel). Struktur — vollständige Komponente:

```jsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

const PLATFORMS = ["KLEINANZEIGEN", "EBAY"];
const PLATFORM_LABELS = { KLEINANZEIGEN: "Kleinanzeigen", EBAY: "eBay" };
const STATUS_LABELS = { ACTIVE: "aktiv", PAUSED: "pausiert", ENDED: "beendet", SOLD: "verkauft", DRAFT: "Entwurf" };

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString("de-DE") : "—";
}

function ActivateForm({ itemId, platform, onDone }) {
  const [price, setPrice] = useState("");
  const [listedAt, setListedAt] = useState(new Date().toISOString().split("T")[0]);
  const [url, setUrl] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [error, setError] = useState(null);

  const create = useMutation({
    mutationFn: () =>
      api.createListing(itemId, {
        platform,
        current_price: Number(price),
        listed_at: listedAt,
        url: url.trim() || null,
        min_price: minPrice === "" ? null : Number(minPrice),
      }),
    onSuccess: onDone,
    onError: (err) => setError(err.message),
  });

  const suggestedMin = price ? Math.round(Number(price) * 0.7 * 100) / 100 : null;

  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-text-secondary">
          Preis (\u20ac)*
          <input type="number" step="0.01" min="0.01" required value={price} onChange={(e) => setPrice(e.target.value)}
            className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
        </label>
        <label className="text-xs text-text-secondary">
          Eingestellt am
          <input type="date" value={listedAt} onChange={(e) => setListedAt(e.target.value)}
            className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
        </label>
      </div>
      <label className="text-xs text-text-secondary block">
        URL zur Anzeige (aus der Zwischenablage einfuegen)
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…"
          className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
      </label>
      <label className="text-xs text-text-secondary block">
        Schmerzgrenze (\u20ac) — darunter schlaegt nichts vor
        <input type="number" step="0.01" min="0.01" value={minPrice} onChange={(e) => setMinPrice(e.target.value)}
          placeholder={suggestedMin ? `Vorschlag: ${suggestedMin}` : ""}
          className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
      </label>
      {error && <p className="text-xs text-no-go">{error}</p>}
      <button type="submit" disabled={create.isPending}
        className="text-xs px-3 py-1.5 rounded-lg font-bold bg-lego-yellow text-black hover:bg-lego-yellow/90">
        Als eingestellt markieren
      </button>
    </form>
  );
}

function OpenListing({ itemId, listing, onDone }) {
  const [priceDraft, setPriceDraft] = useState("");
  const patch = useMutation({
    mutationFn: (data) => api.updateListing(itemId, listing.id, data),
    onSuccess: onDone,
  });
  const end = useMutation({ mutationFn: () => api.endListing(itemId, listing.id), onSuccess: onDone });

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm text-text-primary">
        <span>
          {Math.round(listing.current_price)}\u20ac {listing.price_type === "VB" ? "VB" : ""} · {STATUS_LABELS[listing.status]}
          {" seit "}{formatDate(listing.listed_at)}
          {listing.at_floor && <span className="text-no-go"> · an der Schmerzgrenze</span>}
        </span>
        {listing.url && (
          <a href={listing.url} target="_blank" rel="noreferrer" className="text-xs text-lego-yellow hover:underline">
            Anzeige {"\u2197"}
          </a>
        )}
      </div>
      <div className="flex gap-2 items-center">
        <input type="number" step="0.01" placeholder="Neuer Preis" value={priceDraft}
          onChange={(e) => setPriceDraft(e.target.value)}
          className="w-28 bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
        <button onClick={() => priceDraft && patch.mutate({ current_price: Number(priceDraft) })}
          className="text-xs px-2 py-1 rounded bg-bg-hover text-text-primary border border-border">Preis aendern</button>
        <button onClick={() => patch.mutate({ status: listing.status === "PAUSED" ? "ACTIVE" : "PAUSED" })}
          className="text-xs px-2 py-1 rounded bg-bg-hover text-text-primary border border-border">
          {listing.status === "PAUSED" ? "Fortsetzen" : "Pausieren"}
        </button>
        <button onClick={() => end.mutate()}
          className="text-xs px-2 py-1 rounded bg-bg-hover text-no-go border border-border">Beendet/geloescht</button>
      </div>
      {listing.price_changes.length > 0 && (
        <p className="text-xs text-text-secondary">
          {listing.price_changes.map((c) => `${formatDate(c.changed_at)}: ${Math.round(c.old_price)}\u2192${Math.round(c.new_price)}\u20ac`).join(" · ")}
        </p>
      )}
    </div>
  );
}

export default function ListingManager({ item, onClose, onChanged }) {
  const queryClient = useQueryClient();
  const { data: listings = [] } = useQuery({
    queryKey: ["listings", item.id],
    queryFn: () => api.listListings(item.id),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["listings", item.id] });
    onChanged();
  };

  const history = listings.filter((x) => x.status === "ENDED" || x.status === "SOLD");

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-bg-card border border-border rounded-xl p-4 w-full max-w-lg max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-3">
          <h3 className="font-bold text-text-primary">Listings — {item.set_name}</h3>
          <button onClick={onClose} className="text-text-secondary hover:text-text-primary">\u2715</button>
        </div>
        {PLATFORMS.map((platform) => {
          const open = listings.find(
            (x) => x.platform === platform && (x.status === "ACTIVE" || x.status === "PAUSED" || x.status === "DRAFT"),
          );
          return (
            <div key={platform} className="border-t border-border/50 py-3">
              <p className="text-sm font-bold text-text-primary mb-2">{PLATFORM_LABELS[platform]}</p>
              {open ? (
                <OpenListing itemId={item.id} listing={open} onDone={refresh} />
              ) : (
                <ActivateForm itemId={item.id} platform={platform} onDone={refresh} />
              )}
            </div>
          );
        })}
        {history.length > 0 && (
          <div className="border-t border-border/50 pt-3 mt-1">
            <p className="text-xs font-bold text-text-secondary mb-1">Historie</p>
            {history.map((x) => (
              <p key={x.id} className="text-xs text-text-secondary">
                {PLATFORM_LABELS[x.platform]}: {formatDate(x.listed_at)} eingestellt
                {x.current_price != null && ` fuer ${Math.round(x.current_price)}\u20ac`} · {STATUS_LABELS[x.status]}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Lint + Build**

```bash
cd frontend && npm run lint && npm run build
```

Erwartung: beide grün.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.js frontend/src/components/ListingBadges.jsx frontend/src/components/ListingManager.jsx && git commit -m "feat(frontend): Listing-Badges und -Verwaltung" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Frontend — Inventar-Seite integrieren

**Files:**
- Modify: `frontend/src/pages/Inventar.jsx`
- Check/Modify: `frontend/src/pages/History.jsx` (nutzt dieselbe Response)

**Interfaces:**
- Consumes: `ListingBadges`, `ListingManager`, `api.listProductGroups`, `api.splitInventory` (Task 10); Response-Felder `item_type`, `product_group`, `listings`, nullable `set_number`/`buy_price`/`total_invested` (Task 4/5/6)

- [ ] **Step 1: Imports + State**

In `Inventar.jsx`: `import ListingBadges from "../components/ListingBadges";`, `import ListingManager from "../components/ListingManager";`. Im `Inventar()`-Component: `const [listingItem, setListingItem] = useState(null);` (Item, dessen Manager offen ist), `const [typeFilter, setTypeFilter] = useState("");`, `const [groupFilter, setGroupFilter] = useState("");`, Query `const { data: productGroups = [] } = useQuery({ queryKey: ["productGroups"], queryFn: api.listProductGroups });`.

- [ ] **Step 2: Filter in die Inventar-Query**

Die bestehende items-Query um die Filter erweitern — `queryKey: ["inventory", statusFilter, typeFilter, groupFilter]` und im `queryFn` die Parameter nur gesetzt mitgeben (`{ ...(typeFilter && { item_type: typeFilter }), ...(groupFilter && { product_group: groupFilter }) }`). Über der Liste zwei Selects im Stil der bestehenden Controls: Typ (Alle/Lego/Sonstiges → ``""``/`"LEGO"`/`"GENERIC"`), Warengruppe (Alle + `productGroups`).

- [ ] **Step 3: Karte erweitern**

Pro Karte: `<ListingBadges item={item} />` unter der Preiszeile; Button `Listings` (öffnet `setListingItem(item)`) neben dem bestehenden `SellDropdown`; bei `item.quantity > 1` Button `Teilen` → `api.splitInventory(item.id, 1)` in einer Mutation mit `queryClient.invalidateQueries({ queryKey: ["inventory"] })` (teilt 1 Stück ab — der häufigste Fall; Menge > 1 abzweigen geht durch Wiederholen). Anzeige von `set_number` überall null-sicher: `{item.set_number ?? item.product_group}` als Untertitel; `total_invested`/`buy_price` null-sicher rendern (`item.total_invested != null ? formatMoney(item.total_invested) : "—"`).

Am Component-Ende:

```jsx
{listingItem && (
  <ListingManager
    item={listingItem}
    onClose={() => setListingItem(null)}
    onChanged={() => queryClient.invalidateQueries({ queryKey: ["inventory"] })}
  />
)}
```

- [ ] **Step 4: Anlege-/Edit-Formular**

`emptyAddForm()` um `item_type: "LEGO"` und `product_group: ""` ergänzen. Im Add-Modal als erstes Feld ein Toggle (zwei Buttons oder Select) Lego/Sonstiges. Bei `item_type === "GENERIC"`: `set_number`-Feld ausblenden, stattdessen Warengruppen-Select (`productGroups` + Freitext-Option: ein `<input list="productGroups">` mit `<datalist>`), `buy_price` nicht mehr `required` (Placeholder „unbekannt"). Beim Submit `buy_price: form.buy_price === "" ? null : Number(form.buy_price)` senden. `handleSetNumberChange`-Autofill nur bei Lego aufrufen. Edit-Modal: `product_group` + `search_query` als editierbare Felder ergänzen.

- [ ] **Step 5: Verkauft-Flow — Checkliste**

In `sellMutation.onSuccess` (bekommt die aktualisierte `InventoryResponse`): wenn `response.listings` noch offene Einträge enthält (`status ACTIVE/PAUSED`), `setListingItem(response)` aufrufen und einen Hinweis rendern — im ListingManager-Kopf reicht dafür ein Banner, das bei `item.status === "SOLD"` erscheint:

```jsx
{item.status === "SOLD" && (
  <p className="text-xs text-no-go mb-2">
    Artikel ist verkauft — offene Anzeigen unten mit „Beendet/geloescht" abraeumen!
  </p>
)}
```

(Das Banner gehört in `ListingManager.jsx`, direkt unter die Kopfzeile.) Zusätzlich im Sell-Formular die Plattform-Vorauswahl: wenn das Item offene Listings hat, die Plattformen als Schnellwahl-Buttons über dem `sell_platform`-Feld anbieten (`KLEINANZEIGEN` → „Kleinanzeigen", `EBAY` → „eBay").

- [ ] **Step 6: History.jsx prüfen**

```bash
grep -n "set_number\|total_invested\|buy_price" frontend/src/pages/History.jsx
```

Jede Stelle null-sicher machen (gleiche Muster wie Step 3), da verkaufte GENERIC-Artikel dort auftauchen.

- [ ] **Step 7: Lint + Build + Smoke**

```bash
cd frontend && npm run lint && npm run build
```

Danach mit laufendem Backend (sofern lokal eingerichtet) die Seite im Dev-Server öffnen und prüfen: GENERIC-Artikel ohne Set-Nummer/Kaufpreis anlegen → Karte zeigt Warengruppe und „nicht eingestellt" → Listing markieren (KA, 50 €, URL) → Badge „KA: aktiv seit 0 T · 50€" verlinkt → Verkauft melden (eBay) → Manager öffnet mit Abräum-Banner. Ohne lokale DB entfällt der Smoke — dann trägt die CI.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/Inventar.jsx frontend/src/pages/History.jsx && git commit -m "feat(frontend): Inventar fuer alle Artikel - Filter, Badges, Listing-Flow" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Endabnahme + PR

**Files:** keine neuen — Verifikation und PR.

- [ ] **Step 1: Alles noch einmal**

```bash
cd backend && .venv\Scripts\python.exe -m pytest -q && .venv\Scripts\python.exe -m ruff check app tests
```

```bash
cd frontend && npm run lint && npm run build
```

Erwartung: alles grün. Jeden Fehlschlag fixen, bevor es weitergeht.

- [ ] **Step 2: Branch prüfen + push**

```bash
git branch --show-current && git push -u origin feat/inventar-listings
```

- [ ] **Step 3: PR öffnen**

```bash
gh pr create --title "Inventar fuer alles + manueller Listing-Status (PR 1/3)" --body "Setzt PR 1 der Spec docs/superpowers/specs/2026-08-30-inventar-fuer-alles-design.md um: item_type/Warengruppe/optionaler Kaufpreis, listings-Tabelle mit Historie und Schmerzgrenze, Verkauft-Checkliste, Posten teilen, Settings-Credential-Cleanup (ADR 0002). Migration mit Backfill-Test gegen echte Zeilen. PR 2 (KI + Foto-first) und PR 3 (Tages-Check + Telegram) folgen.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 4: Merge-Gate beachten**

Kein Self-Merge ohne Review: erst Agent-Review (z. B. `/pr-review-lite` bzw. code-review) laufen lassen, Findings fixen, CI abwarten — dann mergen (Repo-Regel). Nach dem Merge deployt `deploy-production.yml` automatisch inkl. `alembic upgrade head`.
