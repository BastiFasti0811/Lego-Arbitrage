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
    tmp_path.mkdir(parents=True, exist_ok=True)
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
async def test_manifest_item_can_carry_purchase_history_and_storage_location(tmp_path, db):
    # Altbestands-Import (z. B. aus einer Jahre alten Einkaufstabelle): im
    # Unterschied zum Dachbodenfund sind Kaufpreis und -plattform bekannt.
    source = _manifest(
        tmp_path,
        [
            {
                "key": "E05",
                "item_type": "LEGO",
                "set_number": "42055",
                "set_name": "Schaufelradbagger",
                "condition": "NEW_SEALED",
                "quantity": 1,
                "buy_date": "2018-12-03",
                "buy_price": 142.82,
                "buy_shipping": 0.0,
                "buy_platform": "amazon.fr",
                "storage_location": "Dachboden Kiste 3",
                "notes": "",
                "photos": [],
                "listings": [],
            }
        ],
    )

    await import_inventory.run(source, db, apply=True)

    item = (await _items(db))[0]
    assert item.buy_price == 142.82
    assert item.buy_platform == "amazon.fr"
    assert item.storage_location == "Dachboden Kiste 3"
    assert item.buy_date == date(2018, 12, 3)


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


# ---------------------------------------------------------------------------
# Review-Findings PR #27
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marker_with_wildcards_does_not_match_a_foreign_item(tmp_path, db):
    # LIKE-Platzhalter im Schluessel: "_" traf jedes Zeichen, Foto und Listing
    # landeten am fremden Posten.
    existing = _manifest(tmp_path / "erst", [_generic(key="EX1")])
    await import_inventory.run(existing, db, apply=True)

    source = _manifest(tmp_path / "zweit", [_generic(key="E_1", set_name="Kaffeemaschine")])
    summary = await import_inventory.run(source, db, apply=True)

    assert summary.created == ["E_1"]
    assert sorted(i.set_name for i in await _items(db)) == ["Jabra Evolve 75e Headset", "Kaffeemaschine"]


@pytest.mark.asyncio
async def test_marker_is_found_even_when_the_note_was_edited(tmp_path, db):
    source = _manifest(tmp_path, [_generic()])
    await import_inventory.run(source, db, apply=True)
    item = (await _items(db))[0]
    item.notes = "Etui fehlt doch. " + item.notes  # wie die Notiz-Bearbeitung in der App
    await db.commit()

    summary = await import_inventory.run(source, db, apply=True)

    assert summary.created == []
    assert len(await _items(db)) == 1


@pytest.mark.asyncio
async def test_unknown_platform_aborts_before_writing(tmp_path, db):
    source = _manifest(
        tmp_path, [_generic(listings=[{"platform": "willhaben", "status": "DRAFT", "title": "T"}])]
    )

    with pytest.raises(import_inventory.ManifestError) as exc_info:
        await import_inventory.run(source, db, apply=True)

    assert "WILLHABEN" in str(exc_info.value)
    assert await _items(db) == []


@pytest.mark.asyncio
async def test_lowercase_platform_is_normalised(tmp_path, db):
    source = _manifest(tmp_path, [_generic(listings=[{"platform": "kleinanzeigen", "status": "DRAFT", "title": "T"}])])

    await import_inventory.run(source, db, apply=True)

    listing = (await db.execute(select(Listing))).scalars().one()
    assert listing.platform == "KLEINANZEIGEN"


@pytest.mark.asyncio
async def test_two_listings_on_the_same_platform_abort_before_writing(tmp_path, db):
    # Sonst haengen zwei offene Zeilen derselben Plattform am Posten: der
    # partial-unique-Index greift nur bei identischer Schreibweise.
    source = _manifest(
        tmp_path,
        [
            _generic(
                listings=[
                    {"platform": "kleinanzeigen", "status": "DRAFT", "title": "Erster"},
                    {"platform": "KLEINANZEIGEN", "status": "DRAFT", "title": "Zweiter"},
                ]
            )
        ],
    )

    with pytest.raises(import_inventory.ManifestError) as exc_info:
        await import_inventory.run(source, db, apply=True)

    assert "KLEINANZEIGEN" in str(exc_info.value)
    assert await _items(db) == []


@pytest.mark.asyncio
async def test_active_listing_activates_an_open_draft(tmp_path, db):
    draft = _manifest(
        tmp_path / "erst",
        [_generic(listings=[{"platform": "KLEINANZEIGEN", "status": "DRAFT", "title": "T"}])],
    )
    await import_inventory.run(draft, db, apply=True)

    source = _manifest(
        tmp_path / "zweit",
        [
            _generic(
                listings=[
                    {
                        "platform": "KLEINANZEIGEN",
                        "status": "ACTIVE",
                        "price": 49.0,
                        "url": "https://www.kleinanzeigen.de/s-anzeige/x/123",
                        "listed_at": "2026-09-18",
                    }
                ]
            )
        ],
    )
    await import_inventory.run(source, db, apply=True)

    listing = (await db.execute(select(Listing))).scalars().one()
    assert listing.status == ListingStatus.ACTIVE.value
    assert listing.current_price == 49.0
    assert listing.title == "T"  # der vorbereitete Text ueberlebt die Aktivierung


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        pytest.param({"quantity": 0}, id="menge-null"),
        pytest.param({"condition": "BANANE"}, id="unbekannter-zustand"),
        pytest.param({"set_name": "X" * 400}, id="name-zu-lang"),
        pytest.param({"photos": ["a.heic"]}, id="kein-bildformat"),
        pytest.param({"photos": [f"p{n}.jpg" for n in range(9)]}, id="zu-viele-fotos"),
        pytest.param(
            {"listings": [{"platform": "EBAY", "status": "DRAFT", "platform_category": "K" * 250}]},
            id="kategorie-zu-lang",
        ),
        pytest.param({"listings": [{"platform": "EBAY", "status": "DRAFT", "title": "T" * 130}]}, id="titel-zu-lang"),
    ],
)
async def test_bad_manifest_values_abort_before_writing(tmp_path, db, item):
    source = _manifest(tmp_path, [_generic(**item)])

    with pytest.raises(import_inventory.ManifestError) as exc_info:
        await import_inventory.run(source, db, apply=True)

    assert "E01" in str(exc_info.value)
    assert await _items(db) == []


@pytest.mark.asyncio
async def test_duplicate_key_aborts_before_writing(tmp_path, db):
    source = _manifest(tmp_path, [_generic(), _generic(set_name="Kaffeemaschine")])

    with pytest.raises(import_inventory.ManifestError) as exc_info:
        await import_inventory.run(source, db, apply=True)

    assert "E01" in str(exc_info.value)
    assert await _items(db) == []


@pytest.mark.asyncio
async def test_oversized_photo_aborts_before_writing(tmp_path, db):
    source = _manifest(tmp_path, [_generic(photos=["gross.jpg"])])
    (tmp_path / "gross.jpg").write_bytes(b"x" * (9 * 1024 * 1024))

    with pytest.raises(import_inventory.ManifestError) as exc_info:
        await import_inventory.run(source, db, apply=True)

    assert "gross.jpg" in str(exc_info.value)
    assert await _items(db) == []


@pytest.mark.asyncio
async def test_manifest_that_is_not_an_object_fails_with_a_clear_error(tmp_path, db):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(import_inventory.ManifestError):
        await import_inventory.run(tmp_path, db, apply=True)


@pytest.mark.asyncio
async def test_lego_item_may_carry_a_long_product_group_that_gets_replaced(tmp_path, db):
    # product_group und search_query setzt InventoryAdd bei Lego selbst; eine
    # Laengenpruefung darauf wuerde den Import ohne Grund abbrechen.
    source = _manifest(
        tmp_path,
        [
            {
                "key": "E04",
                "item_type": "LEGO",
                "set_number": "75192",
                "set_name": "Millennium Falcon",
                "product_group": "X" * 150,
                "condition": "NEW_SEALED",
                "quantity": 1,
                "buy_date": "2026-09-20",
                "notes": "",
                "photos": [],
                "listings": [],
            }
        ],
    )

    await import_inventory.run(source, db, apply=True)

    assert (await _items(db))[0].product_group == "Lego"
