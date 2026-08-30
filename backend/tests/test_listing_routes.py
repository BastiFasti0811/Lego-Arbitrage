"""Branching logic of the listing routes (create/patch/end) — DB-free,
fake-session style like tests/test_inventory_optional_buy_price.py."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.routes.listings import ListingCreate, ListingUpdate, create_listing, end_listing, update_listing
from app.models.listing import Listing, ListingStatus


class _ScalarOneResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    """Fake session for the listing routes: one execute() per _get_item/_get_listing
    call, plus add/commit/rollback/refresh tracking. commit_error lets a test make
    commit() raise, e.g. to simulate the DB partial-unique-index race."""

    def __init__(self, fetch_result, *, commit_error: Exception | None = None):
        self._fetch_result = fetch_result
        self._commit_error = commit_error
        self.added: list = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _query):
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


def _item(listings=None, status="HOLDING"):
    return SimpleNamespace(id=1, listings=listings if listings is not None else [], status=status)


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
