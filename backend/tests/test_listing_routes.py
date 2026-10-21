"""Branching logic of the listing routes (create/patch/end) — DB-free,
fake-session style like tests/test_inventory_optional_buy_price.py."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.ai import AIProviderError, ListingText
from app.api.routes.listings import (
    ListingActivateRequest,
    ListingCreate,
    ListingPrepareRequest,
    ListingUpdate,
    activate_listing,
    create_listing,
    end_listing,
    prepare_listing,
    refresh_listing_text,
    update_listing,
)
from app.models.listing import Listing, ListingStatus
from app.services.listing_prepare import draft_price as _draft_price
from app.services.listing_prepare import prepare_listing_draft


class _ScalarOneResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    """Fake session for the listing routes: one execute() per _get_item/_get_listing
    call, plus add/commit/rollback/refresh tracking. commit_error lets a test make
    commit() raise, e.g. to simulate the DB partial-unique-index race.

    fetch_result is returned for every execute() call — enough for routes that
    only look up one row. refresh_listing_text looks up the listing and then
    the item (two separate queries, since Listing.item isn't eager-loaded and
    async lazy-loading isn't available) — pass fetch_results as an ordered list
    for that case; each execute() call pops the next entry."""

    def __init__(self, fetch_result=None, *, fetch_results: list | None = None, commit_error: Exception | None = None):
        self._fetch_result = fetch_result
        self._fetch_results = list(fetch_results) if fetch_results is not None else None
        self._commit_error = commit_error
        self.added: list = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _query):
        if self._fetch_results is not None:
            return _ScalarOneResult(self._fetch_results.pop(0))
        return _ScalarOneResult(self._fetch_result)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self._commit_error is not None:
            raise self._commit_error
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, obj):
        # Real DB backfills id/created_at via server_default on flush; a transient
        # Listing() built in-memory has both as None, so to_listing_response()
        # would choke without this — simulate what the DB round-trip provides.
        if obj.id is None:
            obj.id = 1
        if obj.created_at is None:
            obj.created_at = datetime(2026, 8, 30, tzinfo=UTC)


def _item(
    listings=None,
    status="HOLDING",
    set_name="LEGO Test-Set",
    condition="NEW_SEALED",
    notes=None,
    current_market_price=None,
    market_price_basis=None,
    ai_price_min=None,
    ai_price_max=None,
    buy_price=None,
    item_type="GENERIC",
    set_number=None,
    quantity=1,
):
    return SimpleNamespace(
        id=1,
        listings=listings if listings is not None else [],
        status=status,
        item_type=item_type,
        set_number=set_number,
        quantity=quantity,
        set_name=set_name,
        condition=condition,
        notes=notes,
        current_market_price=current_market_price,
        market_price_basis=market_price_basis,
        ai_price_min=ai_price_min,
        ai_price_max=ai_price_max,
        buy_price=buy_price,
    )


class _FakeProvider:
    """Ersetzt get_provider(): write_listing liefert einen festen ListingText
    oder wirft die uebergebene Exception (typischerweise AIProviderError)."""

    def __init__(self, text: ListingText | None = None, exc: Exception | None = None):
        self.text = text
        self.exc = exc
        self.calls: list[dict] = []

    async def write_listing(self, *, name, condition, notes, platform, price, price_type, quantity=1):
        self.calls.append(
            {
                "name": name,
                "condition": condition,
                "notes": notes,
                "platform": platform,
                "price": price,
                "price_type": price_type,
                "quantity": quantity,
            }
        )
        if self.exc is not None:
            raise self.exc
        return self.text


def _listing(**overrides):
    base = dict(
        item_id=1,
        platform="KLEINANZEIGEN",
        status=ListingStatus.ACTIVE.value,
        price_type="VB",
        current_price=80.0,
        min_price=50.0,
        check_interval_days=14,
        price_drop_percent=10.0,
    )
    base.update(overrides)
    return Listing(**base)


@pytest.mark.asyncio
async def test_create_rejects_second_open_listing_on_platform():
    existing = SimpleNamespace(platform="KLEINANZEIGEN", status=ListingStatus.ACTIVE.value)
    item = _item(listings=[existing])
    session = _FakeSession(fetch_result=item)
    data = ListingCreate(platform="KLEINANZEIGEN", current_price=50.0)

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_activates_existing_draft_listing():
    """'Als eingestellt markieren' auf eine KI-Draft-Zeile aktiviert SIE, statt
    eine zweite offene Zeile anzulegen — Titel/Body ueberleben die Aktivierung."""
    draft = _listing(
        status=ListingStatus.DRAFT.value,
        title="KI-Titel",
        body="KI-Text",
        platform_category="Spielzeug > Lego",
        current_price=None,
        min_price=None,
    )
    item = _item(listings=[draft])
    session = _FakeSession(fetch_result=item)
    data = ListingCreate(platform="KLEINANZEIGEN", current_price=60.0)

    response = await create_listing(item_id=1, data=data, session=session)

    assert response.status == "ACTIVE"
    assert response.current_price == 60.0
    assert response.min_price == 42.0
    assert response.title == "KI-Titel"
    assert response.body == "KI-Text"
    assert response.platform_category == "Spielzeug > Lego"
    assert session.added == []  # keine zweite Zeile - die DRAFT-Zeile wurde aktiviert
    assert session.committed is True


@pytest.mark.asyncio
async def test_create_still_rejects_paused_listing_on_platform():
    existing = SimpleNamespace(platform="KLEINANZEIGEN", status=ListingStatus.PAUSED.value)
    item = _item(listings=[existing])
    session = _FakeSession(fetch_result=item)
    data = ListingCreate(platform="KLEINANZEIGEN", current_price=50.0)

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_translates_integrity_error_to_400():
    item = _item()
    session = _FakeSession(fetch_result=item, commit_error=IntegrityError("x", None, Exception()))
    data = ListingCreate(platform="KLEINANZEIGEN", current_price=50.0)

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_create_roundtrip_uses_min_price_default():
    item = _item()
    session = _FakeSession(fetch_result=item)
    data = ListingCreate(platform="KLEINANZEIGEN", current_price=50.0)

    response = await create_listing(item_id=1, data=data, session=session)

    assert response.status == "ACTIVE"
    assert response.min_price == 35.0
    assert response.price_type == "VB"


@pytest.mark.asyncio
async def test_patch_rejects_min_price_above_price():
    listing = _listing(status=ListingStatus.ACTIVE.value, current_price=80.0, min_price=50.0)
    session = _FakeSession(fetch_result=listing)
    data = ListingUpdate(min_price=100.0)

    with pytest.raises(HTTPException) as exc_info:
        await update_listing(item_id=1, listing_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_patch_rejects_status_outside_active_paused():
    active = _listing(status=ListingStatus.ACTIVE.value)
    session = _FakeSession(fetch_result=active)
    data = ListingUpdate(status=ListingStatus.ENDED.value)

    with pytest.raises(HTTPException) as exc_info:
        await update_listing(item_id=1, listing_id=1, data=data, session=session)
    assert exc_info.value.status_code == 400

    ended = _listing(status=ListingStatus.ENDED.value)
    session2 = _FakeSession(fetch_result=ended)
    data2 = ListingUpdate(title="Neuer Titel")

    with pytest.raises(HTTPException) as exc_info2:
        await update_listing(item_id=1, listing_id=1, data=data2, session=session2)
    assert exc_info2.value.status_code == 400


@pytest.mark.asyncio
async def test_end_twice_rejected():
    listing = _listing(status=ListingStatus.ENDED.value)
    session = _FakeSession(fetch_result=listing)

    with pytest.raises(HTTPException) as exc_info:
        await end_listing(item_id=1, listing_id=1, session=session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_rejects_sold_item():
    item = _item(status="SOLD")
    session = _FakeSession(fetch_result=item)
    data = ListingCreate(platform="KLEINANZEIGEN", current_price=50.0)

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_rejects_bad_interval_and_drop():
    item = _item()
    session = _FakeSession(fetch_result=item)
    data = ListingCreate(platform="KLEINANZEIGEN", current_price=50.0, check_interval_days=0)

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(item_id=1, data=data, session=session)
    assert exc_info.value.status_code == 400

    item2 = _item()
    session2 = _FakeSession(fetch_result=item2)
    data2 = ListingCreate(platform="KLEINANZEIGEN", current_price=50.0, price_drop_percent=150.0)

    with pytest.raises(HTTPException) as exc_info2:
        await create_listing(item_id=1, data=data2, session=session2)
    assert exc_info2.value.status_code == 400


# ---------------------------------------------------------------------------
# prepare_listing ("Inserat vorbereiten")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_creates_draft_with_ai_text_and_price(monkeypatch):
    item = _item()
    session = _FakeSession(fetch_result=item)
    fake_text = ListingText(title="Guter Titel", body="Guter Text.", platform_category="Spielzeug > Lego")
    fake = _FakeProvider(text=fake_text)
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)
    data = ListingPrepareRequest(platform="kleinanzeigen", price=45.0)

    response = await prepare_listing(item_id=1, data=data, session=session)

    listing = response.listing
    assert listing.status == "DRAFT"
    assert listing.platform == "KLEINANZEIGEN"
    assert listing.title == "Guter Titel"
    assert listing.body == "Guter Text."
    assert listing.platform_category == "Spielzeug > Lego"
    assert listing.price_type == "VB"
    assert listing.current_price == 45.0  # Preisvorschlag steht im Entwurf
    assert listing.listed_at is None  # noch nicht eingestellt
    assert response.text_source == "KI"
    assert response.warning is None
    assert len(session.added) == 1
    assert session.committed is True
    assert fake.calls[0]["price"] == 45.0
    assert fake.calls[0]["price_type"] == "VB"
    assert fake.calls[0]["name"] == "LEGO Test-Set"


@pytest.mark.asyncio
async def test_prepare_takes_price_from_market_when_none_given(monkeypatch):
    item = _item(current_market_price=88.456)
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="T", body="B", platform_category="K"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    response = await prepare_listing(item_id=1, data=ListingPrepareRequest(platform="EBAY"), session=session)

    assert response.listing.current_price == 88.46
    assert response.listing.price_type == "FIXED"


@pytest.mark.asyncio
async def test_prepare_rejects_when_already_active(monkeypatch):
    existing = SimpleNamespace(platform="KLEINANZEIGEN", status=ListingStatus.ACTIVE.value)
    item = _item(listings=[existing])
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="x", body="y", platform_category="z"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)
    data = ListingPrepareRequest(platform="KLEINANZEIGEN", price=45.0)

    with pytest.raises(HTTPException) as exc_info:
        await prepare_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400
    assert "läuft schon" in exc_info.value.detail
    assert fake.calls == []  # kein KI-Aufruf, wenn schon eingestellt
    assert session.committed is False


@pytest.mark.asyncio
async def test_prepare_rejects_unknown_platform(monkeypatch):
    item = _item()
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="x", body="y", platform_category="z"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)
    data = ListingPrepareRequest(platform="GARBAGE", price=45.0)

    with pytest.raises(HTTPException) as exc_info:
        await prepare_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400
    assert fake.calls == []  # kein bezahlter KI-Aufruf fuer eine unbekannte Plattform
    assert session.added == []  # keine unaktivierbare Geisterzeile


@pytest.mark.asyncio
async def test_prepare_rejects_sold_item(monkeypatch):
    item = _item(status="SOLD")
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="x", body="y", platform_category="z"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)
    data = ListingPrepareRequest(platform="KLEINANZEIGEN", price=45.0)

    with pytest.raises(HTTPException) as exc_info:
        await prepare_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400
    assert fake.calls == []
    assert session.added == []


@pytest.mark.asyncio
async def test_prepare_rejects_non_positive_price(monkeypatch):
    item = _item()
    session = _FakeSession(fetch_result=item)
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: _FakeProvider())

    with pytest.raises(HTTPException) as exc_info:
        await prepare_listing(item_id=1, data=ListingPrepareRequest(platform="EBAY", price=0), session=session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_prepare_reuses_existing_draft_text_without_ai_call(monkeypatch):
    """Ein Entwurf aus /lego-eingang hat schon Text und Preis: kein zweiter,
    bezahlter KI-Aufruf, keine zweite Zeile."""
    existing = _listing(
        status=ListingStatus.DRAFT.value,
        title="Eingang-Titel",
        body="Eingang-Text",
        current_price=39.0,
        min_price=None,
    )
    item = _item(listings=[existing], current_market_price=99.0)
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="Neu", body="Neu", platform_category="K"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    response = await prepare_listing(
        item_id=1, data=ListingPrepareRequest(platform="KLEINANZEIGEN"), session=session
    )

    assert response.text_source == "ENTWURF"
    assert response.listing.title == "Eingang-Titel"
    assert response.listing.current_price == 39.0  # Entwurfspreis schlaegt Marktpreis
    assert fake.calls == []
    assert session.added == []


@pytest.mark.asyncio
async def test_prepare_rewrites_draft_text_when_price_changes(monkeypatch):
    """Der Preis steht im Text: neuer Preis, neuer Text."""
    existing = _listing(
        status=ListingStatus.DRAFT.value,
        title="Alter Titel",
        body="Alter Text 39 Euro",
        platform_category="Alte Kategorie",
        current_price=39.0,
        min_price=None,
    )
    item = _item(listings=[existing])
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="Neuer Titel", body="Neuer Text", platform_category="Neue Kategorie"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    response = await prepare_listing(
        item_id=1, data=ListingPrepareRequest(platform="KLEINANZEIGEN", price=35.0), session=session
    )

    assert response.listing.title == "Neuer Titel"
    assert response.listing.current_price == 35.0
    assert fake.calls[0]["price"] == 35.0
    assert session.added == []  # keine zweite Zeile
    assert session.committed is True


@pytest.mark.asyncio
async def test_prepare_falls_back_to_template_when_ai_fails(monkeypatch):
    item = _item(item_type="LEGO", set_number="75192", set_name="Millennium Falcon", condition="USED_COMPLETE")
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(exc=AIProviderError("KI-Dienst nicht erreichbar"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)
    data = ListingPrepareRequest(platform="KLEINANZEIGEN", price=450.0)

    response = await prepare_listing(item_id=1, data=data, session=session)

    assert response.text_source == "VORLAGE"
    assert "KI-Dienst nicht erreichbar" in response.warning
    assert response.listing.status == "DRAFT"
    assert response.listing.title.startswith("LEGO 75192 Millennium Falcon")
    assert "Zustand: Gebraucht, komplett" in response.listing.body
    assert "Preis: 450€ VB" in response.listing.body
    assert response.listing.platform_category is None
    assert session.committed is True


@pytest.mark.asyncio
async def test_prepare_without_any_price_source_is_400(monkeypatch):
    item = _item()  # keine current_market_price, ai_price_min/max, buy_price
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="x", body="y", platform_category="z"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)
    data = ListingPrepareRequest(platform="KLEINANZEIGEN", price=None)

    with pytest.raises(HTTPException) as exc_info:
        await prepare_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400
    assert "Kein Preis ermittelbar" in exc_info.value.detail
    assert fake.calls == []


@pytest.mark.asyncio
async def test_prepare_translates_integrity_error_to_400(monkeypatch):
    item = _item(current_market_price=50.0)
    session = _FakeSession(fetch_result=item, commit_error=IntegrityError("x", {}, Exception("dup")))
    monkeypatch.setattr(
        "app.services.listing_prepare.get_provider",
        lambda: _FakeProvider(text=ListingText(title="T", body="B", platform_category="K")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await prepare_listing(item_id=1, data=ListingPrepareRequest(platform="EBAY"), session=session)

    assert exc_info.value.status_code == 400
    assert session.rolled_back is True


# ---------------------------------------------------------------------------
# activate_listing (URL einfuegen -> aktiv)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_uses_draft_price_today_and_default_floor():
    draft = _listing(status=ListingStatus.DRAFT.value, current_price=60.0, min_price=None, title="T", body="B")
    session = _FakeSession(fetch_results=[draft, _item()])

    response = await activate_listing(
        item_id=1,
        listing_id=1,
        data=ListingActivateRequest(url="  https://www.kleinanzeigen.de/s-anzeige/lego/123-23-456  "),
        session=session,
    )

    assert response.status == "ACTIVE"
    assert response.url == "https://www.kleinanzeigen.de/s-anzeige/lego/123-23-456"
    assert response.current_price == 60.0
    assert response.min_price == 42.0
    assert response.listed_at is not None
    assert response.next_check_at is not None
    assert response.title == "T"
    assert session.committed is True


@pytest.mark.asyncio
async def test_activate_accepts_explicit_price_and_date():
    from datetime import date

    draft = _listing(status=ListingStatus.DRAFT.value, current_price=60.0, min_price=None)
    session = _FakeSession(fetch_results=[draft, _item()])

    response = await activate_listing(
        item_id=1,
        listing_id=1,
        data=ListingActivateRequest(
            url="https://www.kleinanzeigen.de/s-anzeige/x/1", current_price=55.0, listed_at=date(2026, 10, 1)
        ),
        session=session,
    )

    assert response.current_price == 55.0
    assert response.listed_at == date(2026, 10, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "LEGO 75192 Millennium Falcon NEU OVP",  # Text statt Adresse in der Zwischenablage
        "https://www.ebay.de/itm/123",  # andere Plattform
        "ftp://kleinanzeigen.de/x",
        "https://kleinanzeigen.de.evil.example/x",
    ],
)
async def test_activate_rejects_urls_that_do_not_belong_to_the_platform(url):
    draft = _listing(status=ListingStatus.DRAFT.value, current_price=60.0, min_price=None)
    session = _FakeSession(fetch_results=[draft, _item()])

    with pytest.raises(HTTPException) as exc_info:
        await activate_listing(item_id=1, listing_id=1, data=ListingActivateRequest(url=url), session=session)

    assert exc_info.value.status_code == 400
    assert draft.status == ListingStatus.DRAFT.value
    assert session.committed is False


@pytest.mark.asyncio
async def test_activate_accepts_ebay_url_for_ebay_draft():
    draft = _listing(platform="EBAY", status=ListingStatus.DRAFT.value, current_price=80.0, min_price=None)
    session = _FakeSession(fetch_results=[draft, _item()])

    response = await activate_listing(
        item_id=1, listing_id=1, data=ListingActivateRequest(url="https://www.ebay.de/itm/1234567"), session=session
    )

    assert response.status == "ACTIVE"


@pytest.mark.asyncio
async def test_activate_rejects_non_draft_listing():
    active = _listing(status=ListingStatus.ACTIVE.value)
    session = _FakeSession(fetch_results=[active, _item()])

    with pytest.raises(HTTPException) as exc_info:
        await activate_listing(
            item_id=1,
            listing_id=1,
            data=ListingActivateRequest(url="https://www.kleinanzeigen.de/s/1"),
            session=session,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_activate_rejects_draft_without_price():
    draft = _listing(status=ListingStatus.DRAFT.value, current_price=None, min_price=None)
    session = _FakeSession(fetch_results=[draft, _item()])

    with pytest.raises(HTTPException) as exc_info:
        await activate_listing(
            item_id=1,
            listing_id=1,
            data=ListingActivateRequest(url="https://www.kleinanzeigen.de/s/1"),
            session=session,
        )

    assert exc_info.value.status_code == 400
    assert "Preis" in exc_info.value.detail


@pytest.mark.asyncio
async def test_activate_rejects_sold_item():
    draft = _listing(status=ListingStatus.DRAFT.value, current_price=60.0, min_price=None)
    session = _FakeSession(fetch_results=[draft, _item(status="SOLD")])

    with pytest.raises(HTTPException) as exc_info:
        await activate_listing(
            item_id=1,
            listing_id=1,
            data=ListingActivateRequest(url="https://www.kleinanzeigen.de/s/1"),
            session=session,
        )

    assert exc_info.value.status_code == 400
    assert session.committed is False


# ---------------------------------------------------------------------------
# refresh_listing_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_text_rejects_ended_listing():
    listing = _listing(status=ListingStatus.ENDED.value)
    session = _FakeSession(fetch_result=listing)

    with pytest.raises(HTTPException) as exc_info:
        await refresh_listing_text(item_id=1, listing_id=1, session=session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_refresh_text_rejects_sold_item(monkeypatch):
    listing = _listing(status=ListingStatus.ACTIVE.value, current_price=80.0)
    item = _item(status="SOLD")
    session = _FakeSession(fetch_results=[listing, item])
    fake = _FakeProvider(text=ListingText(title="x", body="y", platform_category="z"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    with pytest.raises(HTTPException) as exc_info:
        await refresh_listing_text(item_id=1, listing_id=1, session=session)

    assert exc_info.value.status_code == 400
    assert fake.calls == []


@pytest.mark.asyncio
async def test_refresh_text_uses_current_price_for_active_listing(monkeypatch):
    listing = _listing(status=ListingStatus.ACTIVE.value, current_price=80.0, title="Alt", body="Alt")
    item = _item()
    session = _FakeSession(fetch_results=[listing, item])
    fake = _FakeProvider(text=ListingText(title="Neu", body="Neuer Text", platform_category="Kategorie"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    response = await refresh_listing_text(item_id=1, listing_id=1, session=session)

    assert response.title == "Neu"
    assert response.body == "Neuer Text"
    assert response.platform_category == "Kategorie"
    assert fake.calls[0]["price"] == 80.0
    assert fake.calls[0]["price_type"] == listing.price_type


@pytest.mark.asyncio
async def test_refresh_text_falls_back_to_market_price_for_draft_listing(monkeypatch):
    listing = _listing(status=ListingStatus.DRAFT.value, current_price=None, min_price=None)
    item = _item(current_market_price=99.0)
    session = _FakeSession(fetch_results=[listing, item])
    fake = _FakeProvider(text=ListingText(title="Neu", body="Neuer Text", platform_category="Kategorie"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    response = await refresh_listing_text(item_id=1, listing_id=1, session=session)

    assert fake.calls[0]["price"] == 99.0
    assert response.title == "Neu"


@pytest.mark.asyncio
async def test_refresh_text_without_any_price_source_is_400(monkeypatch):
    listing = _listing(status=ListingStatus.DRAFT.value, current_price=None, min_price=None)
    item = _item()
    session = _FakeSession(fetch_results=[listing, item])
    fake = _FakeProvider(text=ListingText(title="x", body="y", platform_category="z"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    with pytest.raises(HTTPException) as exc_info:
        await refresh_listing_text(item_id=1, listing_id=1, session=session)

    assert exc_info.value.status_code == 400
    assert fake.calls == []


@pytest.mark.asyncio
async def test_refresh_text_maps_provider_error_to_503(monkeypatch):
    listing = _listing(status=ListingStatus.ACTIVE.value, current_price=80.0)
    item = _item()
    session = _FakeSession(fetch_results=[listing, item])
    fake = _FakeProvider(exc=AIProviderError("KI-Dienst nicht erreichbar"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    with pytest.raises(HTTPException) as exc_info:
        await refresh_listing_text(item_id=1, listing_id=1, session=session)

    assert exc_info.value.status_code == 503
    assert session.committed is False


# ---------------------------------------------------------------------------
# _draft_price (reine Fallback-Kette, ohne Session testbar)
# ---------------------------------------------------------------------------


def test_draft_price_prefers_body_price():
    item = _item(current_market_price=200.0, buy_price=10.0)
    assert _draft_price(item, None, 45.0) == 45.0


def test_draft_price_uses_listing_current_price_when_active():
    listing = _listing(status=ListingStatus.ACTIVE.value, current_price=77.0)
    item = _item(current_market_price=200.0)
    assert _draft_price(item, listing, None) == 77.0


def test_draft_price_uses_listing_current_price_when_paused():
    listing = _listing(status=ListingStatus.PAUSED.value, current_price=66.0)
    item = _item(current_market_price=200.0)
    assert _draft_price(item, listing, None) == 66.0


def test_draft_price_ignores_draft_listing_current_price():
    # Ein DRAFT hat i. d. R. current_price=None; selbst wenn nicht, zaehlt fuer
    # DRAFT die Fallback-Kette, nicht der (dann bedeutungslose) Anzeigenpreis.
    listing = _listing(status=ListingStatus.DRAFT.value, current_price=66.0)
    item = _item(current_market_price=200.0)
    assert _draft_price(item, listing, None) == 200.0


def test_draft_price_falls_back_to_market_price():
    item = _item(current_market_price=150.0, ai_price_min=10.0, ai_price_max=20.0, buy_price=5.0)
    assert _draft_price(item, None, None) == 150.0


def test_draft_price_falls_back_to_ai_price_mean_when_both_set():
    item = _item(ai_price_min=40.0, ai_price_max=60.0, buy_price=5.0)
    assert _draft_price(item, None, None) == 50.0


def test_draft_price_skips_ai_price_mean_when_only_one_bound_set():
    item = _item(ai_price_min=40.0, ai_price_max=None, buy_price=20.0)
    assert _draft_price(item, None, None) == 30.0  # buy_price * 1.5, Spanne unvollstaendig


def test_draft_price_falls_back_to_buy_price_times_1_5():
    item = _item(buy_price=20.0)
    assert _draft_price(item, None, None) == 30.0


def test_draft_price_returns_none_when_no_source_available():
    item = _item()
    assert _draft_price(item, None, None) is None


# Zustandsabschlag auf den Marktpreis (Feedback-Plan Rang 7): die Lego-Quellen
# liefern den OVP-Preis, ein gebrauchtes Set ist weniger wert.


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("NEW_SEALED", 100.0),
        ("NEW_OPEN_BOX", 90.0),
        ("USED_COMPLETE", 70.0),
        ("USED_INCOMPLETE", 50.0),
        ("UNKNOWN", 70.0),
    ],
)
def test_draft_price_applies_condition_factor_to_market_price(condition, expected):
    item = _item(
        item_type="LEGO", set_number="10331", condition=condition,
        current_market_price=100.0, market_price_basis="CONSENSUS", buy_price=20.0,
    )
    assert _draft_price(item, None, None) == expected


def test_draft_price_used_item_is_below_market_price():
    item = _item(
        item_type="LEGO", set_number="10331", condition="USED_COMPLETE",
        current_market_price=37.89, market_price_basis="BRICKMERGE_ONLY",
    )
    price = _draft_price(item, None, None)
    assert price == 26.52
    assert price < item.current_market_price


def test_draft_price_discounts_market_price_without_basis():
    # Analyse-Historie schreibt basis=None, liefert aber ebenfalls den Neupreis.
    item = _item(condition="USED_COMPLETE", current_market_price=80.0, market_price_basis=None)
    assert _draft_price(item, None, None) == 56.0


def test_draft_price_keeps_ebay_sold_market_price_unchanged():
    # Freitext-Median mischt die Zustaende schon -- kein zweiter Abschlag.
    item = _item(condition="USED_COMPLETE", current_market_price=80.0, market_price_basis="EBAY_SOLD")
    assert _draft_price(item, None, None) == 80.0


def test_draft_price_does_not_discount_ai_range_or_buy_price():
    used_ai = _item(condition="USED_COMPLETE", ai_price_min=40.0, ai_price_max=60.0)
    used_buy = _item(condition="USED_COMPLETE", buy_price=20.0)
    assert _draft_price(used_ai, None, None) == 50.0
    assert _draft_price(used_buy, None, None) == 30.0


def test_draft_price_does_not_discount_body_or_running_listing_price():
    item = _item(condition="USED_INCOMPLETE", current_market_price=200.0)
    listing = _listing(status=ListingStatus.ACTIVE.value, current_price=77.0)
    assert _draft_price(item, None, 45.0) == 45.0
    assert _draft_price(item, listing, None) == 77.0


@pytest.mark.asyncio
async def test_prepare_draft_for_used_item_uses_discounted_price(monkeypatch):
    provider = _FakeProvider(text=ListingText(title="LEGO 10331", body="Text", platform_category=""))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: provider)
    item = _item(
        item_type="LEGO", set_number="10331", set_name="Eisvogel", condition="USED_COMPLETE",
        current_market_price=100.0, market_price_basis="CONSENSUS",
    )

    prepared = await prepare_listing_draft(item, "KLEINANZEIGEN")

    assert prepared.listing.current_price == 70.0
    assert provider.calls[0]["price"] == 70.0


# ---------------------------------------------------------------------------
# Review-Findings PR #25
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_names_lego_items_with_brand_and_set_number(monkeypatch):
    # Auf eBay/Kleinanzeigen wird nach "LEGO 75192" gesucht; der KI-Titel
    # braucht beides, wie es auch die Vorlage voranstellt.
    item = _item(item_type="LEGO", set_number="75192", set_name="Millennium Falcon")
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="T", body="B", platform_category="K"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    await prepare_listing(item_id=1, data=ListingPrepareRequest(platform="EBAY", price=500.0), session=session)

    assert fake.calls[0]["name"] == "LEGO 75192 Millennium Falcon"


@pytest.mark.asyncio
async def test_prepare_does_not_repeat_set_number_already_in_the_name(monkeypatch):
    item = _item(item_type="LEGO", set_number="40426", set_name="LEGO® 40426 Türkranz / Adventskranz 2in1")
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="T", body="B", platform_category="K"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    await prepare_listing(item_id=1, data=ListingPrepareRequest(platform="EBAY", price=40.0), session=session)

    assert fake.calls[0]["name"] == "LEGO® 40426 Türkranz / Adventskranz 2in1"


@pytest.mark.asyncio
async def test_prepare_passes_quantity_to_provider(monkeypatch):
    item = _item(quantity=3)
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="T", body="B", platform_category="K"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    await prepare_listing(item_id=1, data=ListingPrepareRequest(platform="EBAY", price=20.0), session=session)

    assert fake.calls[0]["quantity"] == 3


@pytest.mark.asyncio
async def test_prepare_truncates_ai_title_to_column_length(monkeypatch):
    # Listing.title ist String(120); PostgreSQL wirft bei Ueberlaenge einen
    # DataError -- nach dem schon bezahlten KI-Aufruf. SQLite prueft das nicht.
    item = _item()
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="X" * 150, body="B", platform_category="K"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    data = ListingPrepareRequest(platform="EBAY", price=20.0)
    response = await prepare_listing(item_id=1, data=data, session=session)

    assert len(response.listing.title) == 120


@pytest.mark.asyncio
async def test_refresh_text_truncates_ai_title_to_column_length(monkeypatch):
    listing = _listing(status=ListingStatus.ACTIVE.value, current_price=80.0)
    item = _item()
    session = _FakeSession(fetch_results=[listing, item])
    fake = _FakeProvider(text=ListingText(title="X" * 150, body="B", platform_category="K"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    await refresh_listing_text(item_id=1, listing_id=1, session=session)

    assert len(listing.title) == 120


@pytest.mark.asyncio
async def test_patch_price_on_draft_listing_sets_price_without_price_change(monkeypatch):
    # Ein Entwurf hat keinen alten Preis (current_price None); apply_price_change
    # schrieb dann eine ListingPriceChange mit old_price=None -> NOT NULL -> 500.
    listing = _listing(status=ListingStatus.DRAFT.value, current_price=None, min_price=None)
    session = _FakeSession(fetch_result=listing)

    response = await update_listing(item_id=1, listing_id=1, data=ListingUpdate(current_price=60.0), session=session)

    assert response.current_price == 60.0
    assert session.added == []
    assert listing.next_check_at is None


@pytest.mark.asyncio
async def test_prepare_rejects_unconfirmed_draft_item(monkeypatch):
    item = _item(status="DRAFT")
    session = _FakeSession(fetch_result=item)
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: _FakeProvider())

    with pytest.raises(HTTPException) as exc_info:
        await prepare_listing(item_id=1, data=ListingPrepareRequest(platform="EBAY", price=20.0), session=session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_rejects_unconfirmed_draft_item():
    item = _item(status="DRAFT")
    session = _FakeSession(fetch_result=item)

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(item_id=1, data=ListingCreate(platform="EBAY", current_price=20.0), session=session)

    assert exc_info.value.status_code == 400
    assert session.committed is False


@pytest.mark.parametrize(
    ("set_name", "expected"),
    [
        ("LEGO Star Wars Millennium Falcon", "LEGO 75192 Star Wars Millennium Falcon"),
        ("LEGO® Millennium Falcon", "LEGO 75192 Millennium Falcon"),
        ("Millennium Falcon 751920 Edition", "LEGO 75192 Millennium Falcon 751920 Edition"),
    ],
)
def test_listing_name_puts_brand_and_set_number_up_front_exactly_once(set_name, expected):
    from app.services.listing_prepare import listing_name as _listing_name

    item = _item(item_type="LEGO", set_number="75192", set_name=set_name)

    assert _listing_name(item) == expected


@pytest.mark.asyncio
async def test_refresh_text_uses_draft_price_suggestion(monkeypatch):
    """Ein vorbereiteter Entwurf traegt seinen Preis; der neue Text nennt denselben."""
    listing = _listing(status=ListingStatus.DRAFT.value, current_price=42.0, min_price=None)
    item = _item(current_market_price=99.0)
    session = _FakeSession(fetch_results=[listing, item])
    fake = _FakeProvider(text=ListingText(title="T", body="B", platform_category="K"))
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: fake)

    await refresh_listing_text(item_id=1, listing_id=1, session=session)

    assert fake.calls[0]["price"] == 42.0
