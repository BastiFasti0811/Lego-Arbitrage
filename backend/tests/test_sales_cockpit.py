"""Verkaufs-Cockpit (Plan KW 45): Kopfzahlen und Kandidaten gegen echte Zeilen."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models import Base
from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus
from app.models.inventory_photo import InventoryPhoto
from app.models.listing import Listing, ListingStatus
from app.models.listing_posting_job import ListingPostingJob, PostingJobStatus
from app.services.sales_cockpit import (
    build_cockpit,
    count_posting_today,
    is_ready_to_post,
    load_cockpit,
    pick_candidates,
)

# Montag 02.11.2026, 10:00 Berlin (MEZ, UTC+1)
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class _AsyncSessionAdapter:
    def __init__(self, sync_session):
        self._session = sync_session

    async def execute(self, statement):
        return self._session.execute(statement)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as sync_session:
        yield sync_session
    engine.dispose()


def _item(
    session,
    *,
    photos=0,
    item_type=InventoryItemType.LEGO.value,
    status=InventoryStatus.HOLDING.value,
    market=None,
    buy_price=50.0,
    listings=(),
    set_number="10001",
):
    item = InventoryItem(
        item_type=item_type,
        set_number=set_number if item_type == InventoryItemType.LEGO.value else None,
        product_group="Lego" if item_type == InventoryItemType.LEGO.value else "Elektronik",
        set_name=f"Posten {set_number}",
        buy_price=buy_price,
        buy_shipping=0.0,
        buy_date=date(2026, 1, 10),
        condition="NEW_SEALED",
        quantity=1,
        status=status,
        current_market_price=market,
        market_price_updated_at=NOW if market is not None else None,
        market_price_basis="CONSENSUS" if market is not None else None,
    )
    session.add(item)
    session.flush()
    for index in range(photos):
        session.add(InventoryPhoto(item_id=item.id, filename=f"p{index}.jpg", sort_order=index))
    for listing_status, price, *platform in listings:
        session.add(
            Listing(
                item_id=item.id,
                platform=platform[0] if platform else "KLEINANZEIGEN",
                status=listing_status,
                price_type="VB",
                title="Titel",
                body="Text",
                current_price=price,
                check_interval_days=14,
                price_drop_percent=10.0,
                listed_at=date(2026, 10, 30) if listing_status != ListingStatus.DRAFT.value else None,
            )
        )
    session.flush()
    session.refresh(item)
    return item


def _job(session, status, *, started=None, finished=None, requested=None, dry_run=False):
    job = ListingPostingJob(
        listing_id=None,
        action="CREATE",
        status=status,
        dry_run=dry_run,
        requested_at=requested or started or NOW,
        started_at=started,
        finished_at=finished,
    )
    session.add(job)
    session.flush()
    return job


def test_without_photo_counts_lego_in_stock_only(session):
    _item(session, photos=0, set_number="1")
    _item(session, photos=0, set_number="2")
    _item(session, photos=2, set_number="3")
    _item(session, photos=0, item_type=InventoryItemType.GENERIC.value, set_number="4")
    _item(session, photos=0, status=InventoryStatus.SOLD.value, set_number="5")

    items = session.query(InventoryItem).all()
    cockpit = build_cockpit(items, [], NOW)

    assert cockpit.lego_holding == 3
    assert cockpit.lego_without_photo == 2


def test_ready_needs_draft_with_price_and_photo_and_no_running_ad(session):
    ready = _item(session, photos=1, listings=[(ListingStatus.DRAFT.value, 99.0)], set_number="1")
    _item(session, photos=0, listings=[(ListingStatus.DRAFT.value, 99.0)], set_number="2")  # ohne Foto
    _item(session, photos=1, listings=[(ListingStatus.DRAFT.value, 0.0)], set_number="3")  # ohne Preis
    _item(
        session,
        photos=1,
        listings=[(ListingStatus.DRAFT.value, 99.0), (ListingStatus.ACTIVE.value, 99.0, "EBAY")],
        set_number="4",
    )  # laeuft schon auf einer anderen Plattform
    generic = _item(
        session,
        photos=1,
        item_type=InventoryItemType.GENERIC.value,
        listings=[(ListingStatus.DRAFT.value, 20.0)],
        set_number="5",
    )

    items = session.query(InventoryItem).all()
    cockpit = build_cockpit(items, [], NOW)

    assert cockpit.drafts_ready == 2
    assert {i.id for i in items if is_ready_to_post(i)} == {ready.id, generic.id}


def test_candidates_are_lego_with_photo_without_open_listing_highest_price_first(session):
    cheap = _item(session, photos=1, market=40.0, set_number="1")
    top = _item(session, photos=1, market=400.0, set_number="2")
    mid = _item(session, photos=1, market=120.0, set_number="3")
    also = _item(session, photos=1, market=90.0, set_number="4")
    _item(session, photos=0, market=900.0, set_number="5")  # ohne Foto
    _item(session, photos=1, market=800.0, listings=[(ListingStatus.DRAFT.value, 800.0)], set_number="6")
    _item(session, photos=1, market=700.0, listings=[(ListingStatus.ACTIVE.value, 700.0)], set_number="7")
    ended = _item(session, photos=1, market=60.0, listings=[(ListingStatus.ENDED.value, 70.0)], set_number="8")
    _item(session, photos=1, market=999.0, item_type=InventoryItemType.GENERIC.value, set_number="9")

    picked = pick_candidates(session.query(InventoryItem).all())

    assert [c.item_id for c in picked] == [top.id, mid.id, also.id]
    assert picked[0].suggested_price == 400.0
    assert picked[0].photo_count == 1
    # Beendete Anzeigen sperren nicht: der Posten kommt wieder an die Reihe.
    rest = pick_candidates(session.query(InventoryItem).all(), limit=10)
    assert [c.item_id for c in rest][-2:] == [ended.id, cheap.id]


def test_candidate_drops_out_once_draft_is_prepared(session):
    item = _item(session, photos=1, market=300.0, set_number="1")
    assert [c.item_id for c in pick_candidates([item])] == [item.id]

    session.add(
        Listing(
            item_id=item.id,
            platform="KLEINANZEIGEN",
            status=ListingStatus.DRAFT.value,
            price_type="VB",
            current_price=300.0,
            check_interval_days=14,
            price_drop_percent=10.0,
        )
    )
    session.flush()
    session.refresh(item)

    assert pick_candidates([item]) == []


def test_posting_today_uses_berlin_day_and_ignores_dry_runs(session):
    berlin_midnight = datetime(2026, 11, 1, 23, 0, tzinfo=UTC)
    _job(session, PostingJobStatus.POSTED.value, started=NOW - timedelta(hours=2), finished=NOW - timedelta(hours=2))
    _job(  # 00:30 Berlin: schon heute
        session,
        PostingJobStatus.POSTED.value,
        started=berlin_midnight + timedelta(minutes=30),
        finished=berlin_midnight + timedelta(minutes=35),
    )
    _job(  # 23:30 Berlin gestern
        session,
        PostingJobStatus.POSTED.value,
        started=berlin_midnight - timedelta(minutes=30),
        finished=berlin_midnight - timedelta(minutes=25),
    )
    _job(session, PostingJobStatus.DRAFT_SAVED.value, started=NOW, finished=NOW, dry_run=True)
    _job(session, PostingJobStatus.FAILED.value, started=NOW - timedelta(hours=1), finished=NOW - timedelta(hours=1))
    _job(session, PostingJobStatus.REQUESTED.value, requested=NOW - timedelta(days=2))
    _job(session, PostingJobStatus.REQUESTED.value)
    _job(session, PostingJobStatus.APPROVED.value)

    today = count_posting_today(session.query(ListingPostingJob).all(), NOW)

    assert today.posted == 2
    assert today.started == 3  # zwei online, einer fehlgeschlagen; der Testlauf zaehlt nicht
    assert today.max_per_day == 3
    assert today.failed == 1
    assert today.waiting_approval == 2  # auch der von vorgestern
    assert today.queued == 1
    assert today.block is not None and "Tageslimit" in today.block


async def test_route_returns_cockpit_from_database(session):
    from app.api.routes import cockpit as cockpit_routes

    _item(session, photos=0, market=200.0, set_number="1")
    top = _item(session, photos=3, market=250.0, set_number="2")
    _item(session, photos=1, market=150.0, listings=[(ListingStatus.DRAFT.value, 150.0)], set_number="3")
    _item(session, photos=0, status=InventoryStatus.SOLD.value, set_number="4")
    _job(session, PostingJobStatus.POSTED.value, started=datetime.now(UTC), finished=datetime.now(UTC))
    _job(session, PostingJobStatus.CANCELLED.value, requested=datetime(2026, 1, 1, tzinfo=UTC))
    session.commit()

    response = await cockpit_routes.sales_cockpit(session=_AsyncSessionAdapter(session))

    assert response.lego_holding == 3
    assert response.lego_without_photo == 1
    assert response.drafts_ready == 1
    assert response.posting_today.posted == 1
    assert response.posting_today.waiting_approval == 0
    assert [c.item_id for c in response.candidates] == [top.id]
    assert response.candidates[0].photo_count == 3


async def test_load_cockpit_skips_old_finished_jobs(session):
    _job(
        session,
        PostingJobStatus.POSTED.value,
        started=datetime(2026, 10, 1, tzinfo=UTC),
        finished=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.commit()

    cockpit = await load_cockpit(_AsyncSessionAdapter(session), NOW)

    assert cockpit.posting_today.posted == 0
    assert cockpit.posting_today.started == 0
    assert cockpit.posting_today.block is None
