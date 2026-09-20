"""Import aus dem Eingang-Workflow (Spec: 2026-09-20-eingang-workflow-design.md).

Echtes In-Memory-SQLite wie in test_ai_endpoints.py -- der Import haengt an
Spalten-Defaults, dem Notiz-Marker und echten WHERE-Klauseln, eine Attrappe
davon wuerde nur sich selbst bestaetigen.
"""

import base64
import json
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base
from app.models.inventory import InventoryItem
from app.models.inventory_photo import InventoryPhoto
from app.models.listing import Listing, ListingStatus
from app.tools import import_inventory

_PIXEL = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


class _AsyncSessionAdapter:
    def __init__(self, sync_session):
        self._session = sync_session

    async def execute(self, statement):
        return self._session.execute(statement)

    def add(self, obj):
        self._session.add(obj)

    async def delete(self, obj):
        self._session.delete(obj)

    async def commit(self):
        self._session.commit()

    async def rollback(self):
        self._session.rollback()

    async def refresh(self, obj):
        self._session.refresh(obj)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as sync_session:
        yield _AsyncSessionAdapter(sync_session)
    engine.dispose()


def _manifest(tmp_path, items, mark="Eingang 2026-09-20"):
    for item in items:
        for name in item.get("photos", []):
            (tmp_path / name).write_bytes(_PIXEL)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"mark": mark, "items": items}), encoding="utf-8"
    )
    return tmp_path


def _generic(**overrides):
    base = {
        "key": "E01",
        "item_type": "GENERIC",
        "set_name": "Jabra Evolve 75e Headset",
        "product_group": "Elektronik",
        "condition": "USED_COMPLETE",
        "quantity": 1,
        "buy_date": "2026-09-20",
        "notes": "Etui dabei.",
        "photos": [],
        "listings": [],
    }
    base.update(overrides)
    return base


async def _items(db):
    return (await db.execute(select(InventoryItem))).scalars().all()


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(tmp_path, db):
    source = _manifest(tmp_path, [_generic(photos=["a.jpg"])])

    summary = await import_inventory.run(source, db, apply=False)

    assert summary.would_create == ["E01"]
    assert await _items(db) == []


@pytest.mark.asyncio
async def test_apply_creates_item_with_photos_and_draft_listing(tmp_path, db):
    source = _manifest(
        tmp_path,
        [
            _generic(
                photos=["a.jpg", "b.jpg"],
                listings=[
                    {
                        "platform": "KLEINANZEIGEN",
                        "status": "DRAFT",
                        "title": "Jabra Evolve 75e – Bluetooth-Headset mit Etui",
                        "body": "Gepflegtes Headset, Etui und Kurzanleitung dabei.",
                        "platform_category": "Elektronik > Audio & Hifi",
                    }
                ],
            )
        ],
    )

    summary = await import_inventory.run(source, db, apply=True)

    item = (await _items(db))[0]
    assert summary.created == ["E01"]
    assert item.set_name == "Jabra Evolve 75e Headset"
    assert item.status == "HOLDING"
    assert item.notes.startswith("[Eingang 2026-09-20 E01]")
    assert item.buy_date == date(2026, 9, 20)

    photos = (await db.execute(select(InventoryPhoto))).scalars().all()
    assert [p.original_filename for p in photos] == ["a.jpg", "b.jpg"]

    listing = (await db.execute(select(Listing))).scalars().one()
    assert listing.status == ListingStatus.DRAFT.value
    assert listing.title.startswith("Jabra Evolve 75e")
    assert listing.current_price is None


@pytest.mark.asyncio
async def test_apply_is_repeatable_without_duplicates(tmp_path, db):
    source = _manifest(tmp_path, [_generic(photos=["a.jpg"])])
    await import_inventory.run(source, db, apply=True)

    summary = await import_inventory.run(source, db, apply=True)

    assert summary.created == []
    assert summary.skipped == ["E01"]
    assert len(await _items(db)) == 1
    assert len((await db.execute(select(InventoryPhoto))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_lego_item_keeps_set_number_and_new_sealed(tmp_path, db):
    # Lego wird als neu verkauft; die Fotos dienen nur der Inventarisierung.
    source = _manifest(
        tmp_path,
        [
            {
                "key": "E02",
                "item_type": "LEGO",
                "set_number": "75192",
                "set_name": "Millennium Falcon",
                "condition": "NEW_SEALED",
                "quantity": 2,
                "buy_date": "2026-09-20",
                "notes": "",
                "photos": ["falcon.jpg"],
                "listings": [],
            }
        ],
    )

    await import_inventory.run(source, db, apply=True)

    item = (await _items(db))[0]
    assert item.item_type == "LEGO"
    assert item.set_number == "75192"
    assert item.product_group == "Lego"
    assert item.condition == "NEW_SEALED"
    assert item.quantity == 2


@pytest.mark.asyncio
async def test_active_listing_from_manifest_keeps_url_and_price(tmp_path, db):
    source = _manifest(
        tmp_path,
        [
            _generic(
                listings=[
                    {
                        "platform": "EBAY",
                        "status": "ACTIVE",
                        "price": 25.0,
                        "price_type": "FIXED",
                        "url": "https://www.ebay.de/itm/267777011327",
                        "listed_at": "2026-09-01",
                    }
                ]
            )
        ],
    )

    await import_inventory.run(source, db, apply=True)

    listing = (await db.execute(select(Listing))).scalars().one()
    assert listing.status == ListingStatus.ACTIVE.value
    assert listing.current_price == 25.0
    assert listing.url.endswith("267777011327")
    assert listing.listed_at == date(2026, 9, 1)


@pytest.mark.asyncio
async def test_missing_photo_aborts_before_writing(tmp_path, db):
    source = _manifest(tmp_path, [_generic(photos=["a.jpg"])])
    (tmp_path / "a.jpg").unlink()

    with pytest.raises(import_inventory.ManifestError) as exc_info:
        await import_inventory.run(source, db, apply=True)

    assert "E01" in str(exc_info.value)
    assert await _items(db) == []


@pytest.mark.asyncio
async def test_lego_without_set_number_aborts_before_writing(tmp_path, db):
    source = _manifest(
        tmp_path,
        [_generic(key="E03", item_type="LEGO", set_number=None, photos=["a.jpg"])],
    )

    with pytest.raises(import_inventory.ManifestError) as exc_info:
        await import_inventory.run(source, db, apply=True)

    assert "E03" in str(exc_info.value)
    assert await _items(db) == []
