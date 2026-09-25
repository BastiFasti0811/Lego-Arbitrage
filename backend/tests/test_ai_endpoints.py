"""Foto-first-Flow (PR 2, Task 6): Draft anlegen, KI analysiert, Confirm, Neubewertung.

Provider und Scraper sind komplett gefaked — kein Netzzugriff, kein echtes SDK.
Zwei Fake-Session-Stile je nach Bedarf: `_ItemSession` (SimpleNamespace-Item,
wie `_MarkAsSoldSession` in test_inventory_optional_buy_price.py) fuer Tests,
die nur `_get_item` (plus bei /analyze `_product_group_choices`) durchlaufen;
`db_session` (echtes In-Memory-SQLite, wie in test_inventory_valuation_api.py)
fuer /draft und list_inventory, wo echte Spalten-Defaults bzw. eine echte
WHERE-Klausel gebraucht werden, keine Attrappe davon.

Drei mitgefuehrte Auftraege aus dem Review frueherer Tasks stecken mit drin:
- list_inventory blendet DRAFT ohne expliziten status-Parameter aus.
- /revalue unterscheidet totes eBay (503) von "nichts gefunden" (404).
- AnalyzeRequest.hints ist laengenbegrenzt (Field max_length=500).
"""

import dataclasses
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ai import AIProviderError, ItemDraft
from app.api.routes import inventory
from app.models import Base
from app.models.inventory import PRODUCT_GROUP_SUGGESTIONS, InventoryItem
from app.scrapers.base import ScrapedPrice, UndecodableResponseError

_ITEM_DRAFT = ItemDraft(
    name="Bosch PSB 500 RE Schlagbohrmaschine",
    product_group="Elektronik",
    condition="USED_COMPLETE",
    description="Gebrauchte Schlagbohrmaschine, augenscheinlich funktionsfaehig.",
    search_query="Bosch PSB 500 RE",
    platform_category="Heimwerken > Werkzeuge",
    price_min=35.0,
    price_max=55.0,
    confidence="medium",
)

_SCRAPED_PRICE = ScrapedPrice(
    source="EBAY_SOLD",
    price_eur=45.0,
    median_price=45.0,
    min_price=40.0,
    max_price=50.0,
    sold_count=5,
    source_url="https://www.ebay.de/sch/i.html?_nkw=Bosch+PSB+500+RE",
    is_reliable=True,
    notes="Median from 5 sold items",
)

# So sieht die Antwort aus, wenn die Sold-Suche blockiert ist (Prod-Normalfall,
# 403) und get_price_for_query auf aktive Sofort-Kaufen-Angebote ausweicht.
_ACTIVE_PRICE = ScrapedPrice(
    source="EBAY_ACTIVE",
    price_eur=39.0,
    median_price=39.0,
    min_price=25.0,
    max_price=60.0,
    sold_count=7,
    source_url="https://www.ebay.de/sch/i.html?_nkw=Bosch+PSB+500+RE&LH_BIN=1",
    is_reliable=False,
    notes="Fallback: Median aus 7 aktiven BIN-Listungen",
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Ersetzt get_provider(): analyze_photos liefert einen festen Entwurf
    oder wirft die uebergebene Exception (typischerweise AIProviderError)."""

    def __init__(self, draft=None, exc=None):
        self.draft = draft
        self.exc = exc
        self.calls: list[dict] = []

    async def analyze_photos(self, photos, hints, product_groups):
        self.calls.append({"photos": photos, "hints": hints, "product_groups": product_groups})
        if self.exc is not None:
            raise self.exc
        return self.draft


class _FakeScraper:
    """Ersetzt EbaySoldScraper: async-Context-Manager wie im echten Scraper,
    get_price_for_query liefert einen festen Preis oder wirft die Exception."""

    def __init__(self, price=None, exc=None):
        self.price = price
        self.exc = exc
        self.queries: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get_price_for_query(self, query):
        self.queries.append(query)
        if self.exc is not None:
            raise self.exc
        return self.price


class _DualResult:
    """Bedient in einem Objekt sowohl _get_item (scalar_one_or_none) als auch
    _product_group_choices (all()) — /analyze braucht in einem Request beide
    Abfrageformen, die Reihenfolge der zwei execute()-Aufrufe ist damit egal."""

    def __init__(self, item, product_group_rows):
        self._item = item
        self._rows = product_group_rows

    def scalar_one_or_none(self):
        return self._item

    def all(self):
        return self._rows


class _ItemSession:
    """Fake-Session fuer analyze/confirm/revalue: ein Item, optionale
    Warengruppen-Zeilen fuer die /analyze-interne Produktgruppen-Abfrage."""

    def __init__(self, item, product_group_rows=()):
        self._item = item
        self._product_group_rows = product_group_rows
        self.committed = False
        self.refreshed = False

    async def execute(self, _query):
        return _DualResult(self._item, self._product_group_rows)

    async def commit(self):
        self.committed = True

    async def refresh(self, _item):
        self.refreshed = True


class _AsyncSessionAdapter:
    """Reicht Aufrufe an eine echte synchrone SQLAlchemy-Session durch
    (gleiches Muster wie test_inventory_valuation_api.py)."""

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

    async def refresh(self, obj):
        self._session.refresh(obj)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as sync_session:
        yield _AsyncSessionAdapter(sync_session)
    engine.dispose()


def _item(**overrides):
    base = dict(
        id=1,
        item_type="GENERIC",
        set_number=None,
        set_name="Neuer Artikel",
        theme=None,
        image_url=None,
        product_group="Diverses",
        search_query=None,
        ai_price_min=None,
        ai_price_max=None,
        ai_analysis_at=None,
        buy_price=None,
        buy_shipping=0.0,
        buy_date=date(2026, 8, 1),
        buy_platform=None,
        buy_url=None,
        reference_url=None,
        condition="NEW_SEALED",
        quantity=1,
        notes=None,
        storage_location=None,
        market_price_basis=None,
        photos=[],
        listings=[],
        current_market_price=None,
        market_price_updated_at=None,
        unrealized_profit=None,
        unrealized_roi_percent=None,
        sell_signal_active=False,
        sell_signal_reason=None,
        status="DRAFT",
        sell_price=None,
        sell_date=None,
        sell_platform=None,
        realized_profit=None,
        realized_roi_percent=None,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_db_item(status, **overrides):
    defaults = dict(
        item_type="GENERIC", set_name=f"Artikel-{status}", product_group="Diverses",
        status=status, buy_date=date(2026, 8, 1),
    )
    defaults.update(overrides)
    return InventoryItem(**defaults)


def _photo(filename="a.jpg", content_type="image/jpeg", id=1):  # noqa: A002 -- matches InventoryPhoto.id
    return SimpleNamespace(filename=filename, content_type=content_type, id=id)


# ---------------------------------------------------------------------------
# POST /draft
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_draft_item_sets_draft_status(db_session):
    result = await inventory.create_draft_item(session=db_session)

    assert result.status == "DRAFT"
    assert result.item_type == "GENERIC"
    assert result.set_name == "Neuer Artikel"
    assert result.product_group == "Diverses"
    assert result.buy_date == date.today()


# ---------------------------------------------------------------------------
# list_inventory: DRAFT-Ausschluss ohne expliziten status-Parameter
# (mitgefuehrter Auftrag aus dem Task-2-Review)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_inventory_excludes_draft_by_default(db_session):
    db_session.add(_make_db_item("DRAFT"))
    db_session.add(_make_db_item("HOLDING"))
    db_session.add(_make_db_item("SOLD"))
    await db_session.commit()

    result = await inventory.list_inventory(
        status=None, item_type=None, product_group=None,
        sort_by="buy_date", limit=100, offset=0, session=db_session,
    )

    statuses = {item.status for item in result}
    assert statuses == {"HOLDING", "SOLD"}
    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_inventory_explicit_status_draft_still_works(db_session):
    db_session.add(_make_db_item("DRAFT"))
    db_session.add(_make_db_item("HOLDING"))
    await db_session.commit()

    result = await inventory.list_inventory(
        status="DRAFT", item_type=None, product_group=None,
        sort_by="buy_date", limit=100, offset=0, session=db_session,
    )

    assert len(result) == 1
    assert result[0].status == "DRAFT"


# ---------------------------------------------------------------------------
# POST /{item_id}/analyze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_rejects_item_without_photos(monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    item = _item(photos=[])
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(hints=None), session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_analyze_skips_missing_photo_files_and_400s_when_none_remain(monkeypatch, tmp_path):
    # Foto-Zeile existiert in der DB, aber die Datei fehlt auf der Platte --
    # ein anderer Fall als "gar keine Fotos", derselbe 400 am Ende.
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    item = _item(photos=[_photo("ghost.jpg")])
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(hints=None), session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_analyze_skips_undecodable_photo(monkeypatch, tmp_path):
    # Reale prepare_photo() -- bewusst nicht gemockt --, damit Pillows echtes
    # UnidentifiedImageError (eine OSError-Unterklasse) tatsaechlich durch den
    # Handler laeuft, statt nur eine Attrappe zu bestaetigen. Ein gueltiges
    # Bild neben der Datenmuell-Datei zeigt, dass nur die kaputte uebersprungen
    # wird, nicht die ganze Analyse abbricht.
    from PIL import Image

    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    photo_dir = tmp_path / "1"
    photo_dir.mkdir()
    Image.new("RGB", (50, 50), color=(10, 20, 30)).save(photo_dir / "good.jpg", "JPEG")
    (photo_dir / "bad.jpg").write_bytes(b"not an image")

    fake_provider = _FakeProvider(draft=_ITEM_DRAFT)
    monkeypatch.setattr("app.api.routes.inventory.get_provider", lambda: fake_provider)
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: _FakeScraper(price=None))

    item = _item(photos=[_photo("good.jpg", id=1), _photo("bad.jpg", id=2)])
    session = _ItemSession(item)

    response = await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(hints=None), session)

    assert response.draft.name == _ITEM_DRAFT.name
    assert len(fake_provider.calls) == 1
    assert len(fake_provider.calls[0]["photos"]) == 1


@pytest.mark.asyncio
async def test_analyze_rejects_when_all_photos_are_undecodable(monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    photo_dir = tmp_path / "1"
    photo_dir.mkdir()
    (photo_dir / "bad.jpg").write_bytes(b"not an image")

    item = _item(photos=[_photo("bad.jpg", id=1)])
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(hints=None), session)

    # Dieselbe deutsche Meldung wie der "gar keine Fotos"-Fall -- fuer den
    # Aufrufer ist "alle Dateien kaputt" ununterscheidbar von "keine da".
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Bitte zuerst Fotos hochladen"


@pytest.mark.asyncio
async def test_analyze_happy_path_sets_ai_fields_and_returns_draft_plus_ebay(monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    photo_dir = tmp_path / "1"
    photo_dir.mkdir()
    (photo_dir / "a.jpg").write_bytes(b"fake-jpeg-bytes")
    monkeypatch.setattr(
        "app.api.routes.inventory.prepare_photo",
        lambda path, content_type: (b"prepared-bytes", content_type or "image/jpeg"),
    )
    fake_provider = _FakeProvider(draft=_ITEM_DRAFT)
    monkeypatch.setattr("app.api.routes.inventory.get_provider", lambda: fake_provider)
    fake_scraper = _FakeScraper(price=_SCRAPED_PRICE)
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: fake_scraper)

    item = _item(photos=[_photo("a.jpg")])
    # "Werkzeuge" steckt nicht in PRODUCT_GROUP_SUGGESTIONS -- nur so zeigt der
    # Test, dass die DB-Warengruppe wirklich mit der Startliste vereinigt wird.
    session = _ItemSession(item, product_group_rows=[("Werkzeuge",)])

    response = await inventory.analyze_inventory_item(
        1, inventory.AnalyzeRequest(hints="Funktioniert einwandfrei"), session
    )

    assert item.ai_price_min == 35.0
    assert item.ai_price_max == 55.0
    assert item.ai_analysis_at is not None
    assert session.committed is True

    assert response.draft.name == _ITEM_DRAFT.name
    assert response.draft.description == _ITEM_DRAFT.description
    assert response.draft.platform_category == _ITEM_DRAFT.platform_category
    assert response.ebay is not None
    assert response.ebay.median == 45.0
    assert response.ebay.sold_count == 5
    assert response.ebay.is_reliable is True
    assert response.ebay.source_url == _SCRAPED_PRICE.source_url
    assert response.ebay_error is None

    # Die eBay-Suche nutzt die Query aus der KI-Antwort, nicht irgendein
    # Feld vom Item -- die Query entsteht ja erst durch die Analyse.
    assert fake_scraper.queries == [_ITEM_DRAFT.search_query]
    assert fake_provider.calls[0]["hints"] == "Funktioniert einwandfrei"
    assert fake_provider.calls[0]["product_groups"] == sorted({"Werkzeuge"} | set(PRODUCT_GROUP_SUGGESTIONS))


@pytest.mark.asyncio
@pytest.mark.parametrize(("price", "expected_source"), [(_SCRAPED_PRICE, "EBAY_SOLD"), (_ACTIVE_PRICE, "EBAY_ACTIVE")])
async def test_analyze_reports_whether_ebay_median_is_sales_or_asking_prices(
    monkeypatch, tmp_path, price, expected_source
):
    # Ohne Quelle beschriftet der Foto-first-Dialog auch den Aktiv-Fallback
    # als "Verkaeufe" -- Angebotspreise sehen dann aus wie erzielte Preise.
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    photo_dir = tmp_path / "1"
    photo_dir.mkdir()
    (photo_dir / "a.jpg").write_bytes(b"fake-jpeg-bytes")
    monkeypatch.setattr(
        "app.api.routes.inventory.prepare_photo",
        lambda path, content_type: (b"prepared-bytes", content_type or "image/jpeg"),
    )
    monkeypatch.setattr("app.api.routes.inventory.get_provider", lambda: _FakeProvider(draft=_ITEM_DRAFT))
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: _FakeScraper(price=price))

    session = _ItemSession(_item(photos=[_photo("a.jpg")]), product_group_rows=[])

    response = await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(), session)

    assert response.ebay.source == expected_source


@pytest.mark.asyncio
async def test_analyze_limits_to_four_photos(monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    photo_dir = tmp_path / "1"
    photo_dir.mkdir()
    filenames = [f"{i}.jpg" for i in range(5)]
    for name in filenames:
        (photo_dir / name).write_bytes(b"fake")

    prepared_paths: list[str] = []

    def _fake_prepare(path, content_type):
        prepared_paths.append(path.name)
        return (b"bytes", content_type or "image/jpeg")

    monkeypatch.setattr("app.api.routes.inventory.prepare_photo", _fake_prepare)
    monkeypatch.setattr("app.api.routes.inventory.get_provider", lambda: _FakeProvider(draft=_ITEM_DRAFT))
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: _FakeScraper(price=None))

    item = _item(photos=[_photo(name) for name in filenames])
    session = _ItemSession(item)

    await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(hints=None), session)

    assert len(prepared_paths) == 4
    assert prepared_paths == filenames[:4]


@pytest.mark.asyncio
async def test_analyze_raises_503_when_provider_unavailable(monkeypatch, tmp_path):
    # Fehlender API-Key: get_provider() selbst wirft, bevor analyze_photos
    # ueberhaupt aufgerufen wird -- muss denselben 503-Pfad nehmen.
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    photo_dir = tmp_path / "1"
    photo_dir.mkdir()
    (photo_dir / "a.jpg").write_bytes(b"fake")
    monkeypatch.setattr(
        "app.api.routes.inventory.prepare_photo", lambda path, content_type: (b"bytes", "image/jpeg")
    )

    def _raise_provider_error():
        raise AIProviderError("ANTHROPIC_API_KEY fehlt — in backend/.env setzen")

    monkeypatch.setattr("app.api.routes.inventory.get_provider", _raise_provider_error)

    item = _item(photos=[_photo("a.jpg")])
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(hints=None), session)

    assert exc_info.value.status_code == 503
    assert "ANTHROPIC_API_KEY" in exc_info.value.detail
    assert session.committed is False


@pytest.mark.asyncio
async def test_analyze_raises_503_when_analyze_photos_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    photo_dir = tmp_path / "1"
    photo_dir.mkdir()
    (photo_dir / "a.jpg").write_bytes(b"fake")
    monkeypatch.setattr(
        "app.api.routes.inventory.prepare_photo", lambda path, content_type: (b"bytes", "image/jpeg")
    )
    fake_provider = _FakeProvider(exc=AIProviderError("Die KI hat die Analyse dieser Fotos abgelehnt"))
    monkeypatch.setattr("app.api.routes.inventory.get_provider", lambda: fake_provider)

    item = _item(photos=[_photo("a.jpg")])
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(hints=None), session)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Die KI hat die Analyse dieser Fotos abgelehnt"
    assert session.committed is False


@pytest.mark.asyncio
async def test_analyze_surfaces_ebay_error_but_keeps_draft_when_source_is_dead(monkeypatch, tmp_path):
    # Der teure KI-Aufruf ist bereits gelaufen, wenn eBay stirbt -- der
    # Entwurf darf nicht verschwinden, nur die Marktdaten fehlen (Wahl aus
    # den drei mitgefuehrten Auftraegen: ebay_error statt Gesamt-503).
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    photo_dir = tmp_path / "1"
    photo_dir.mkdir()
    (photo_dir / "a.jpg").write_bytes(b"fake")
    monkeypatch.setattr(
        "app.api.routes.inventory.prepare_photo", lambda path, content_type: (b"bytes", "image/jpeg")
    )
    monkeypatch.setattr("app.api.routes.inventory.get_provider", lambda: _FakeProvider(draft=_ITEM_DRAFT))
    fake_scraper = _FakeScraper(exc=UndecodableResponseError("boom"))
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: fake_scraper)

    item = _item(photos=[_photo("a.jpg")])
    session = _ItemSession(item)

    response = await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(hints=None), session)

    assert response.draft.name == _ITEM_DRAFT.name
    assert response.ebay is None
    assert response.ebay_error == "eBay ist derzeit nicht erreichbar"
    assert item.ai_price_min == _ITEM_DRAFT.price_min
    assert session.committed is True


def test_analyze_request_hints_is_length_capped():
    inventory.AnalyzeRequest(hints="a" * 500)

    with pytest.raises(ValidationError):
        inventory.AnalyzeRequest(hints="a" * 501)


# ---------------------------------------------------------------------------
# POST /{item_id}/confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_rejects_non_draft_item():
    item = _item(status="HOLDING", set_name="Bosch PSB 500 RE")
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.confirm_draft_item(1, session)

    assert exc_info.value.status_code == 400
    assert session.committed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("set_name", ["Neuer Artikel", "  Neuer Artikel  ", ""])
async def test_confirm_rejects_unedited_draft_name(set_name):
    item = _item(status="DRAFT", set_name=set_name)
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.confirm_draft_item(1, session)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Bitte erst Artikeldaten speichern"


@pytest.mark.asyncio
async def test_confirm_happy_path_moves_draft_to_holding():
    item = _item(status="DRAFT", set_name="Bosch PSB 500 RE")
    session = _ItemSession(item)

    response = await inventory.confirm_draft_item(1, session)

    assert item.status == "HOLDING"
    assert response.status == "HOLDING"
    assert session.committed is True


# ---------------------------------------------------------------------------
# POST /{item_id}/revalue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revalue_rejects_lego_item():
    item = _item(item_type="LEGO", set_number="75192", search_query="LEGO 75192")
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.revalue_inventory_item(1, session)

    assert exc_info.value.status_code == 400
    assert session.committed is False


@pytest.mark.asyncio
async def test_revalue_rejects_generic_without_search_query():
    item = _item(item_type="GENERIC", search_query=None)
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.revalue_inventory_item(1, session)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "search_query fehlt"


@pytest.mark.asyncio
async def test_revalue_happy_path_sets_market_price(monkeypatch):
    fake_scraper = _FakeScraper(price=_SCRAPED_PRICE)
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: fake_scraper)

    item = _item(
        item_type="GENERIC", search_query="Bosch PSB 500 RE",
        buy_price=30.0, buy_shipping=5.0, status="HOLDING",
    )
    session = _ItemSession(item)

    response = await inventory.revalue_inventory_item(1, session)

    assert item.current_market_price == 45.0
    assert item.market_price_updated_at is not None
    assert item.unrealized_profit == 10.0  # 45.0 - (30.0 + 5.0)
    assert response.current_market_price == 45.0
    assert fake_scraper.queries == ["Bosch PSB 500 RE"]
    assert session.committed is True


@pytest.mark.asyncio
async def test_revalue_404_when_no_sales_found(monkeypatch):
    fake_scraper = _FakeScraper(price=None)
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: fake_scraper)

    item = _item(item_type="GENERIC", search_query="Nonexistent Query Xyzzy")
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.revalue_inventory_item(1, session)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Keine eBay-Verkaeufe zu dieser Suche gefunden"
    assert session.committed is False


@pytest.mark.asyncio
async def test_revalue_does_not_store_asking_prices_as_market_value(monkeypatch):
    # Angebotspreise gehoeren nicht in die Geldzahlen (Commit fa5e2a3). Liefert
    # die Suche nur den Aktiv-Fallback, bleibt der alte Marktwert stehen und
    # der Nutzer erfaehrt das Angebotsniveau nur als Hinweis.
    fake_scraper = _FakeScraper(price=_ACTIVE_PRICE)
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: fake_scraper)

    item = _item(
        item_type="GENERIC", search_query="Bosch PSB 500 RE",
        buy_price=30.0, buy_shipping=5.0, status="HOLDING",
        current_market_price=50.0,
    )
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.revalue_inventory_item(1, session)

    assert exc_info.value.status_code == 404
    assert "39" in exc_info.value.detail
    assert "7 aktive" in exc_info.value.detail
    assert item.current_market_price == 50.0
    assert session.committed is False


@pytest.mark.asyncio
async def test_revalue_503_when_source_is_undecodable(monkeypatch):
    fake_scraper = _FakeScraper(exc=UndecodableResponseError("boom"))
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: fake_scraper)

    item = _item(item_type="GENERIC", search_query="Bosch PSB 500 RE")
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.revalue_inventory_item(1, session)

    # Der entscheidende Unterschied zum 404-Test oben: eine tote Quelle ist
    # nicht "nichts gefunden" (Commit b647429 / Task-4-Review).
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "eBay ist derzeit nicht erreichbar"
    assert session.committed is False


@pytest.mark.asyncio
async def test_revalue_503_when_source_has_transport_error(monkeypatch):
    fake_scraper = _FakeScraper(exc=httpx.HTTPError("boom"))
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: fake_scraper)

    item = _item(item_type="GENERIC", search_query="Bosch PSB 500 RE")
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.revalue_inventory_item(1, session)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "eBay ist derzeit nicht erreichbar"


# ---------------------------------------------------------------------------
# Review-Findings PR #25
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revalue_does_not_store_too_few_sales_as_market_value(monkeypatch):
    # Zwei Treffer der ungefilterten Freitextsuche (Zubehoer inklusive) sind
    # kein Marktwert; get_price_for_query markiert das mit is_reliable=False.
    few_sales = dataclasses.replace(_SCRAPED_PRICE, sold_count=2, is_reliable=False)
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: _FakeScraper(price=few_sales))

    item = _item(item_type="GENERIC", search_query="Bosch PSB 500 RE", status="HOLDING", current_market_price=50.0)
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.revalue_inventory_item(1, session)

    assert exc_info.value.status_code == 404
    assert "2" in exc_info.value.detail
    assert item.current_market_price == 50.0
    assert session.committed is False


@pytest.mark.asyncio
async def test_analyze_skips_decompression_bomb_photo(monkeypatch, tmp_path):
    # DecompressionBombError erbt nicht von OSError -- ohne eigenen Zweig bricht
    # ein riesiges Bild die ganze Analyse mit 500 ab.
    from PIL import Image

    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    photo_dir = tmp_path / "1"
    photo_dir.mkdir()
    (photo_dir / "good.jpg").write_bytes(b"x")
    (photo_dir / "bomb.png").write_bytes(b"x")

    def fake_prepare(path, content_type):
        if path.name == "bomb.png":
            raise Image.DecompressionBombError("zu viele Pixel")
        return b"prepared-bytes", "image/jpeg"

    monkeypatch.setattr("app.api.routes.inventory.prepare_photo", fake_prepare)
    fake_provider = _FakeProvider(draft=_ITEM_DRAFT)
    monkeypatch.setattr("app.api.routes.inventory.get_provider", lambda: fake_provider)
    monkeypatch.setattr("app.api.routes.inventory.EbaySoldScraper", lambda: _FakeScraper(price=None))

    item = _item(photos=[_photo("good.jpg", id=1), _photo("bomb.png", content_type="image/png", id=2)])

    await inventory.analyze_inventory_item(1, inventory.AnalyzeRequest(hints=None), _ItemSession(item))

    assert len(fake_provider.calls[0]["photos"]) == 1


@pytest.mark.asyncio
async def test_create_draft_removes_abandoned_drafts_older_than_a_day(db_session, monkeypatch, tmp_path):
    # Schliesst jemand den Tab nach dem Upload, bleiben Entwurf und Fotos liegen
    # -- unsichtbar, weil Liste und Summary DRAFT ausblenden. Der naechste
    # Entwurf raeumt alles auf, was aelter als ein Tag ist.
    monkeypatch.setattr("app.api.routes.inventory.PHOTO_STORAGE_ROOT", tmp_path)
    now = datetime.now(UTC)

    def add(status, age):
        row = InventoryItem(
            item_type="GENERIC", set_name="Neuer Artikel", product_group="Diverses",
            status=status, buy_date=date.today(), created_at=now - age,
        )
        db_session.add(row)
        return row

    stale = add("DRAFT", timedelta(days=2))
    fresh = add("DRAFT", timedelta(hours=1))
    held = add("HOLDING", timedelta(days=30))
    await db_session.commit()
    (tmp_path / str(stale.id)).mkdir()
    (tmp_path / str(stale.id) / "a.jpg").write_bytes(b"x")

    created = await inventory.create_draft_item(session=db_session)

    remaining = {row.id for row in (await db_session.execute(select(InventoryItem))).scalars()}
    assert stale.id not in remaining
    assert {fresh.id, held.id, created.id} <= remaining
    assert not (tmp_path / str(stale.id)).exists()


@pytest.mark.asyncio
async def test_mark_as_sold_rejects_unconfirmed_draft():
    # Ein verkaufter Entwurf zaehlte in /history und total_realized_profit,
    # obwohl er nie bestaetigt wurde.
    item = _item(status="DRAFT")
    session = _ItemSession(item)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.mark_as_sold(1, inventory.SellRequest(sell_price=50.0), session)

    assert exc_info.value.status_code == 400
    assert item.status == "DRAFT"
    assert session.committed is False
