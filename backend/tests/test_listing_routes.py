"""Branching logic of the listing routes (create/patch/end) — DB-free,
fake-session style like tests/test_inventory_optional_buy_price.py."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.ai import AIProviderError, ListingText
from app.api.routes.listings import (
    ListingCreate,
    ListingDraftRequest,
    ListingUpdate,
    _draft_price,
    create_listing,
    draft_listing,
    end_listing,
    refresh_listing_text,
    update_listing,
)
from app.models.listing import Listing, ListingStatus


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
    ai_price_min=None,
    ai_price_max=None,
    buy_price=None,
):
    return SimpleNamespace(
        id=1,
        listings=listings if listings is not None else [],
        status=status,
        set_name=set_name,
        condition=condition,
        notes=notes,
        current_market_price=current_market_price,
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

    async def write_listing(self, *, name, condition, notes, platform, price, price_type):
        self.calls.append(
            {
                "name": name,
                "condition": condition,
                "notes": notes,
                "platform": platform,
                "price": price,
                "price_type": price_type,
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
# draft_listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_creates_new_draft_row_with_ai_text(monkeypatch):
    item = _item()
    session = _FakeSession(fetch_result=item)
    fake_text = ListingText(title="Guter Titel", body="Guter Text.", platform_category="Spielzeug > Lego")
    fake = _FakeProvider(text=fake_text)
    monkeypatch.setattr("app.api.routes.listings.get_provider", lambda: fake)
    data = ListingDraftRequest(platform="kleinanzeigen", price=45.0)

    response = await draft_listing(item_id=1, data=data, session=session)

    assert response.status == "DRAFT"
    assert response.platform == "KLEINANZEIGEN"
    assert response.title == "Guter Titel"
    assert response.body == "Guter Text."
    assert response.platform_category == "Spielzeug > Lego"
    assert response.price_type == "VB"
    assert len(session.added) == 1
    assert session.committed is True
    assert fake.calls[0]["price"] == 45.0
    assert fake.calls[0]["price_type"] == "VB"
    assert fake.calls[0]["name"] == "LEGO Test-Set"


@pytest.mark.asyncio
async def test_draft_rejects_when_already_active(monkeypatch):
    existing = SimpleNamespace(platform="KLEINANZEIGEN", status=ListingStatus.ACTIVE.value)
    item = _item(listings=[existing])
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="x", body="y", platform_category="z"))
    monkeypatch.setattr("app.api.routes.listings.get_provider", lambda: fake)
    data = ListingDraftRequest(platform="KLEINANZEIGEN", price=45.0)

    with pytest.raises(HTTPException) as exc_info:
        await draft_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400
    assert "Text-Refresh" in exc_info.value.detail
    assert fake.calls == []  # kein KI-Aufruf, wenn schon eingestellt
    assert session.committed is False


@pytest.mark.asyncio
async def test_draft_overwrites_existing_draft_instead_of_new_row(monkeypatch):
    existing = _listing(
        status=ListingStatus.DRAFT.value,
        title="Alter Titel",
        body="Alter Text",
        platform_category="Alte Kategorie",
        current_price=None,
        min_price=None,
    )
    item = _item(listings=[existing])
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="Neuer Titel", body="Neuer Text", platform_category="Neue Kategorie"))
    monkeypatch.setattr("app.api.routes.listings.get_provider", lambda: fake)
    data = ListingDraftRequest(platform="KLEINANZEIGEN", price=39.0)

    response = await draft_listing(item_id=1, data=data, session=session)

    assert response.title == "Neuer Titel"
    assert response.body == "Neuer Text"
    assert response.platform_category == "Neue Kategorie"
    assert session.added == []  # keine zweite Zeile
    assert session.committed is True


@pytest.mark.asyncio
async def test_draft_maps_provider_error_to_503(monkeypatch):
    item = _item()
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(exc=AIProviderError("KI-Dienst nicht erreichbar"))
    monkeypatch.setattr("app.api.routes.listings.get_provider", lambda: fake)
    data = ListingDraftRequest(platform="KLEINANZEIGEN", price=45.0)

    with pytest.raises(HTTPException) as exc_info:
        await draft_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "KI-Dienst nicht erreichbar"
    assert session.added == []
    assert session.committed is False


@pytest.mark.asyncio
async def test_draft_without_any_price_source_is_400(monkeypatch):
    item = _item()  # keine current_market_price, ai_price_min/max, buy_price
    session = _FakeSession(fetch_result=item)
    fake = _FakeProvider(text=ListingText(title="x", body="y", platform_category="z"))
    monkeypatch.setattr("app.api.routes.listings.get_provider", lambda: fake)
    data = ListingDraftRequest(platform="KLEINANZEIGEN", price=None)

    with pytest.raises(HTTPException) as exc_info:
        await draft_listing(item_id=1, data=data, session=session)

    assert exc_info.value.status_code == 400
    assert "Kein Preis ermittelbar" in exc_info.value.detail
    assert fake.calls == []


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
async def test_refresh_text_uses_current_price_for_active_listing(monkeypatch):
    listing = _listing(status=ListingStatus.ACTIVE.value, current_price=80.0, title="Alt", body="Alt")
    item = _item()
    session = _FakeSession(fetch_results=[listing, item])
    fake = _FakeProvider(text=ListingText(title="Neu", body="Neuer Text", platform_category="Kategorie"))
    monkeypatch.setattr("app.api.routes.listings.get_provider", lambda: fake)

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
    monkeypatch.setattr("app.api.routes.listings.get_provider", lambda: fake)

    response = await refresh_listing_text(item_id=1, listing_id=1, session=session)

    assert fake.calls[0]["price"] == 99.0
    assert response.title == "Neu"


@pytest.mark.asyncio
async def test_refresh_text_without_any_price_source_is_400(monkeypatch):
    listing = _listing(status=ListingStatus.DRAFT.value, current_price=None, min_price=None)
    item = _item()
    session = _FakeSession(fetch_results=[listing, item])
    fake = _FakeProvider(text=ListingText(title="x", body="y", platform_category="z"))
    monkeypatch.setattr("app.api.routes.listings.get_provider", lambda: fake)

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
    monkeypatch.setattr("app.api.routes.listings.get_provider", lambda: fake)

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
