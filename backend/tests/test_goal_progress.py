"""Zielzaehler, Wochen-Digest und Tagesbericht (Tickets 43e6be1a, a99f3f07).

Zaehllogik rein mit SimpleNamespace; die Abfragen laufen zusaetzlich gegen
echte SQLite-Zeilen, damit Filter und Spalten stimmen.
"""

import importlib.util
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.api.routes.goals import to_response
from app.api.routes.settings import NOTIFICATION_TASKS
from app.models import AuctionDeal, InventoryItem, LegoSet, Listing, Offer
from app.models.inventory_photo import InventoryPhoto
from app.models.listing import ListingPriceChange
from app.notifications.delivery import NotificationDeliveryError
from app.notifications.telegram_bot import DailyBestDeal, format_daily_summary, format_goal_digest
from app.services import auction_scan_state
from app.services.goal_progress import (
    BERLIN,
    auction_deals_query,
    build_digest,
    build_progress,
    count_deals,
    count_sales,
    deal_offers_query,
    lego_items_query,
    protocol_segment,
    week_of,
)
from app.services.heartbeat import MONITORED_TASKS
from app.tasks import analyze_new, catawiki_scan, goal_digest
from app.tasks.analyze_new import pick_best_deal
from app.tasks.celery_app import celery_app

# Mittwoch, 21.10.2026, 12:00 Berlin (CEST). Laufende Woche: KW 43, 19.–25.10.
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)


def _berlin(y, m, d, h=12, minute=0):
    return datetime(y, m, d, h, minute, tzinfo=BERLIN).astimezone(UTC)


def _offer(recommendation, discovered_at):
    return SimpleNamespace(recommendation=recommendation, discovered_at=discovered_at)


def _listing(listed_at):
    return SimpleNamespace(listed_at=listed_at)


def _item(*listed, status="HOLDING", item_type="LEGO", sell_date=None):
    return SimpleNamespace(
        status=status, item_type=item_type, sell_date=sell_date, listings=[_listing(d) for d in listed]
    )


# ── Wochen ────────────────────────────────────────────────────────────


def test_week_runs_monday_to_monday_in_berlin_time():
    week = week_of(NOW)
    assert week.label == "KW 43 (19.10.–25.10.)"
    assert week.start == datetime(2026, 10, 19, 0, 0, tzinfo=BERLIN)
    # Sonntag 23:30 Berlin gehoert noch dazu, obwohl es in UTC 22:30 ist.
    assert week.contains(_berlin(2026, 10, 25, 23, 30))
    assert not week.contains(_berlin(2026, 10, 26, 0, 0))
    assert not week.contains(_berlin(2026, 10, 18, 23, 59))


def test_week_over_the_clock_change_has_169_hours():
    # 25.10.2026 endet die Sommerzeit. Gleiche tzinfo rechnet Python in
    # Wanduhrzeit, also in UTC vergleichen.
    week = week_of(NOW)
    assert week.end.astimezone(UTC) - week.start.astimezone(UTC) == timedelta(hours=169)
    assert week.end == datetime(2026, 10, 26, 0, 0, tzinfo=BERLIN)
    # Montag 00:30 Berlin (CET) = Sonntag 23:30 UTC: schon die naechste Woche.
    assert week_of(datetime(2026, 10, 25, 23, 30, tzinfo=UTC)).label.startswith("KW 44")


def test_previous_week_and_naive_timestamps():
    previous = week_of(NOW).previous()
    assert previous.label == "KW 42 (12.10.–18.10.)"
    # SQLite liefert zeitzonenlos; gespeichert wird UTC.
    assert previous.contains(datetime(2026, 10, 18, 21, 0))


# ── Deals ─────────────────────────────────────────────────────────────


def test_only_go_and_go_star_and_released_auctions_count_as_deals():
    week = week_of(NOW)
    offers = [
        _offer("GO_STAR", _berlin(2026, 10, 19, 8)),
        _offer("GO", _berlin(2026, 10, 20)),
        _offer("GO", _berlin(2026, 10, 18, 23)),  # Vorwoche
        _offer("CHECK", _berlin(2026, 10, 21)),
        _offer("CHECK", _berlin(2026, 10, 21)),
        _offer("NO_GO", _berlin(2026, 10, 21)),
    ]
    auctions = [SimpleNamespace(found_at=_berlin(2026, 10, 22)), SimpleNamespace(found_at=_berlin(2026, 10, 1))]

    count = count_deals(offers, auctions, week)

    assert (count.go_star, count.go, count.auctions, count.checks) == (1, 1, 1, 2)
    assert count.total == 3  # CHECK zaehlt nicht


# ── Verkauf ───────────────────────────────────────────────────────────


def test_listed_counts_the_first_listing_only_and_sold_by_sell_date():
    week = week_of(NOW)
    items = [
        _item(date(2026, 10, 20)),  # neu inseriert diese Woche
        _item(date(2026, 9, 1), date(2026, 10, 21)),  # Neueinstellung, war schon inseriert
        _item(None),  # nur Entwurf
        _item(date(2026, 10, 1), status="SOLD", sell_date=date(2026, 10, 25)),
        _item(status="SOLD", sell_date=date(2026, 10, 19)),  # privat verkauft, nie inseriert
        _item(date(2026, 10, 20), item_type="GENERIC"),
        _item(date(2026, 10, 20), status="DRAFT"),
    ]

    count = count_sales(items, week)

    assert (count.listed, count.sold) == (1, 2)


def test_progress_totals_pace_and_protocol_segment():
    items = [_item(date(2026, 10, 20)), _item(date(2026, 9, 1), status="SOLD", sell_date=date(2026, 9, 20)), _item()]
    offers = [_offer("GO", _berlin(2026, 10, 20))]

    progress = build_progress(items, offers, [], NOW)

    assert (progress.lego_items, progress.lego_listed, progress.lego_sold) == (3, 2, 1)
    assert protocol_segment(progress) == "Zielstand: 2/30, 1/15, Deals diese Woche 1/3"
    # 21.10. bis 1.12. = 41 Tage = 6 angebrochene Wochen.
    assert (progress.days_left, progress.weeks_left) == (41, 6)
    assert progress.per_week_needed(progress.listed_missing) == pytest.approx(28 / 6)


def test_pace_after_the_deadline_is_none_not_a_division_by_zero():
    progress = build_progress([], [], [], datetime(2026, 12, 3, tzinfo=UTC))
    assert progress.days_left == 0
    assert progress.per_week_needed(5) is None
    assert progress.per_week_needed(0) == 0.0


def test_api_response_carries_both_weeks():
    progress = build_progress([_item(date(2026, 10, 13))], [_offer("GO", _berlin(2026, 10, 14))], [], NOW)
    response = to_response(progress)
    assert response.current_week.label.startswith("KW 43") and response.current_week.iso_week == 43
    assert (response.previous_week.deals, response.previous_week.listed) == (1, 1)
    assert response.current_week.deals == 0


# ── Digest ────────────────────────────────────────────────────────────


def test_digest_reports_the_finished_week_against_the_one_before():
    monday = datetime(2026, 10, 26, 6, 50, tzinfo=UTC)  # Montag 07:50 Berlin
    offers = [
        _offer("GO", _berlin(2026, 10, 20)),
        _offer("GO_STAR", _berlin(2026, 10, 24)),
        _offer("CHECK", _berlin(2026, 10, 24)),
        _offer("GO", _berlin(2026, 10, 14)),
        _offer("GO", _berlin(2026, 10, 26, 7)),  # laufende Woche, nicht im Bericht
    ]
    items = [_item(date(2026, 10, 21)), _item(date(2026, 10, 22)), _item(date(2026, 10, 15))]

    digest = build_digest(items, offers, [], monday)
    text = format_goal_digest(digest)

    assert digest.reported.week.label == "KW 43 (19.10.–25.10.)"
    assert (digest.reported.deals.total, digest.before.deals.total) == (2, 1)
    assert "2/3 · Vorwoche 1 (▲ +1)" in text
    assert "Grenzfälle (CHECK, nicht gemeldet): 1" in text
    assert "Inseriert 3/3 · Ziel 30: fehlen 27" in text
    assert "Woche +2 · Vorwoche +1 (▲ +1)" in text
    assert "Verkauft 0/15" in text


def test_digest_without_checks_has_no_check_line_and_shows_decline():
    monday = datetime(2026, 10, 26, 6, 50, tzinfo=UTC)
    digest = build_digest([], [_offer("GO", _berlin(2026, 10, 14))], [], monday)
    text = format_goal_digest(digest)
    assert "CHECK" not in text
    assert "0/3 · Vorwoche 1 (▼ −1)" in text and "⚠️" in text


def test_digest_task_fails_loudly_when_telegram_does_not_deliver(monkeypatch):
    digest = build_digest([], [], [], NOW)
    monkeypatch.setattr(goal_digest, "load_goal_digest", AsyncMock(return_value=digest))
    monkeypatch.setattr(goal_digest, "send_goal_digest", AsyncMock(return_value=False))
    monkeypatch.setattr(goal_digest, "async_session", _NullSession)

    with pytest.raises(NotificationDeliveryError):
        goal_digest.send_goal_digest_task()


def test_digest_is_scheduled_monday_before_the_listing_pack_and_monitored():
    entry = celery_app.conf.beat_schedule["goal-digest"]
    assert entry["task"] == "app.tasks.goal_digest.send_goal_digest_task"
    schedule = entry["schedule"]
    assert schedule.day_of_week == {1} and schedule.hour == {7} and schedule.minute == {50}
    assert entry["task"] in MONITORED_TASKS
    assert entry["task"] in NOTIFICATION_TASKS
    assert entry["task"] in celery_app.tasks


# ── Tagesbericht: Bester Deal (a99f3f07) ──────────────────────────────


def _row(recommendation, roi, set_number="10316", set_name="Bruchtal", price=200.0):
    offer = SimpleNamespace(
        recommendation=recommendation, estimated_roi=roi, total_price_eur=None, price_eur=price,
        shipping_eur=6.99, platform="KLEINANZEIGEN", offer_url=f"https://example.org/{set_number}",
    )
    return offer, SimpleNamespace(set_number=set_number, set_name=set_name)


def test_best_deal_is_the_highest_roi_go_offer():
    rows = [_row("GO", 31.0, "1"), _row("GO_STAR", 48.5, "2"), _row("CHECK", 90.0, "3"), _row("GO", None, "4")]
    best = pick_best_deal(rows)
    assert best.set_number == "2" and best.recommendation == "GO_STAR"
    assert best.price == pytest.approx(206.99)
    assert pick_best_deal([_row("CHECK", 90.0)]) is None


def test_daily_summary_shows_the_best_deal_escaped_and_the_week_count():
    best = DailyBestDeal(
        set_number="76417", set_name="Gringotts <Sammler> & Co_", recommendation="GO_STAR",
        roi_percent=48.5, price=1234.5, platform="EBAY", url="https://example.org/a?b=1&c=2",
    )
    text = format_daily_summary(12, 2, best, deals_this_week=1, deals_goal=3)
    assert "Bester Deal ⭐" in text
    assert "Gringotts &lt;Sammler&gt; &amp; Co_" in text
    assert 'href="https://example.org/a?b=1&amp;c=2"' in text
    assert "ROI 48,5 % · 1.234 € · EBAY" in text  # Python rundet 1234,5 auf gerade ab
    assert "Geprüfte Deals diese Woche: 1/3" in text
    assert "Potentieller Gewinn" not in text


def test_daily_summary_without_go_deal_has_no_best_deal_section():
    assert "Bester Deal" not in format_daily_summary(5, 0)


async def test_daily_summary_task_passes_the_best_deal(monkeypatch):
    rows = [_row("GO", 31.0, "1"), _row("GO_STAR", 48.5, "2"), _row("CHECK", 90.0, "3")]

    class _Result:
        def all(self):
            return rows

    class _Session(_NullSession):
        async def execute(self, statement):
            return _Result()

    monkeypatch.setattr(analyze_new, "async_session", _Session)
    monkeypatch.setattr(analyze_new, "load_goal_progress", AsyncMock(return_value=build_progress([], [], [], NOW)))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(analyze_new, "send_daily_summary", send)

    result = await analyze_new._send_summary_async()

    kwargs = send.call_args.kwargs
    assert kwargs["offers_rated"] == 3 and kwargs["go_deals"] == 2
    assert kwargs["best_deal"].set_number == "2"
    assert (kwargs["deals_this_week"], kwargs["deals_goal"]) == (0, 3)
    assert result["best_deal"] == "2"


# ── Abfragen gegen echte Zeilen ───────────────────────────────────────


_TABLES = [
    LegoSet.__table__, Offer.__table__, InventoryItem.__table__, InventoryPhoto.__table__,
    Listing.__table__, ListingPriceChange.__table__, AuctionDeal.__table__,
]


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for table in _TABLES:
        table.create(engine)
    session = Session(engine, expire_on_commit=False)
    yield session
    session.close()
    engine.dispose()


def _add_offer(session, lego_set, recommendation, discovered_at, url):
    session.add(Offer(
        set_id=lego_set.id, platform="KLEINANZEIGEN", offer_url=url, offer_title="x", price_eur=10.0,
        discovered_at=discovered_at, last_seen_at=discovered_at, recommendation=recommendation,
    ))


def test_queries_select_the_right_rows(db):
    lego_set = LegoSet(set_number="10316", set_name="Bruchtal", theme="Icons", release_year=2023)
    db.add(lego_set)
    db.flush()
    _add_offer(db, lego_set, "GO", _berlin(2026, 10, 20), "a")
    _add_offer(db, lego_set, "CHECK", _berlin(2026, 10, 20), "b")
    _add_offer(db, lego_set, "NO_GO", _berlin(2026, 10, 20), "c")
    _add_offer(db, lego_set, None, _berlin(2026, 10, 20), "d")
    _add_offer(db, lego_set, "GO", _berlin(2026, 9, 1), "e")  # zu alt
    listed = InventoryItem(set_number="10316", set_name="Bruchtal", buy_date=date(2026, 1, 1), status="HOLDING")
    sold = InventoryItem(set_number="75192", set_name="Falcon", buy_date=date(2026, 1, 1), status="SOLD",
                         sell_date=date(2026, 10, 13))
    draft = InventoryItem(set_number="1", set_name="Entwurf", buy_date=date(2026, 1, 1), status="DRAFT")
    generic = InventoryItem(set_name="Toaster", item_type="GENERIC", product_group="Haushalt",
                            buy_date=date(2026, 1, 1), status="HOLDING")
    db.add_all([listed, sold, draft, generic])
    db.flush()
    db.add(Listing(item_id=listed.id, platform="KLEINANZEIGEN", status="ACTIVE", price_type="VB",
                   listed_at=date(2026, 10, 20)))
    db.add(AuctionDeal(platform="CATAWIKI", source_url="lot", found_at=_berlin(2026, 10, 21)))
    db.commit()

    since = week_of(NOW).previous().start
    items = db.execute(lego_items_query()).scalars().all()
    offers = db.execute(deal_offers_query(since)).all()
    auctions = db.execute(auction_deals_query(since)).all()
    progress = build_progress(items, offers, auctions, NOW)

    assert sorted(i.set_number for i in items) == ["10316", "75192"]
    assert sorted(o.recommendation for o in offers) == ["CHECK", "GO"]
    assert (progress.lego_items, progress.lego_listed, progress.lego_sold) == (2, 1, 1)
    assert (progress.current.deals.total, progress.current.deals.checks) == (2, 1)
    assert (progress.current.sales.listed, progress.previous.sales.sold) == (1, 1)


# ── Auktionsfreigaben festhalten ──────────────────────────────────────


@pytest.fixture
def deal_store(monkeypatch, db):
    class _Async:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            return db.execute(statement)

        async def commit(self):
            db.commit()

    monkeypatch.setattr(auction_scan_state, "async_session", _Async)
    monkeypatch.setattr(auction_scan_state, "insert", sqlite_insert)
    return db


async def test_bid_ready_lots_are_recorded_once_with_their_first_week(deal_store):
    ready = {"source_url": "https://www.catawiki.com/de/l/1", "can_bid_now": True, "set_number": "10316",
             "lot_title": "LEGO 10316", "current_bid": 90.0, "recommended_max_bid": 150.0,
             "expected_profit_current": 40.0}
    review = {"source_url": "https://www.catawiki.com/de/l/2", "can_bid_now": False}
    first = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

    assert await auction_scan_state.record_bid_ready("CATAWIKI", [ready, review], now=first) == 1
    assert await auction_scan_state.record_bid_ready("CATAWIKI", [ready], now=NOW) == 0

    rows = deal_store.query(AuctionDeal).all()
    assert len(rows) == 1
    assert rows[0].found_at.replace(tzinfo=UTC) == first
    assert (rows[0].set_number, rows[0].max_bid) == ("10316", 150.0)
    assert await auction_scan_state.record_bid_ready("CATAWIKI", [review]) == 0


async def test_a_failing_deal_counter_never_blocks_the_alert(monkeypatch):
    monkeypatch.setattr(catawiki_scan, "record_bid_ready", AsyncMock(side_effect=RuntimeError("db weg")))
    monkeypatch.setattr(catawiki_scan, "unnotified_results", AsyncMock(return_value=[{"source_url": "lot"}]))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(catawiki_scan, "send_auction_discovery_summary", send)
    monkeypatch.setattr(catawiki_scan, "mark_notified", AsyncMock())
    summary = {"notified": 0, "skipped": [], "errors": []}

    await catawiki_scan._notify_new_results("CATAWIKI", [{"source_url": "lot", "can_bid_now": True}], summary, True)

    send.assert_awaited_once()
    assert summary["notified"] == 1 and summary["errors"] == []


def test_migration_matches_model_and_rolls_back():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/b6d1f0a4c7e3_auction_deals.py"
    spec = importlib.util.spec_from_file_location("auction_deals_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with create_engine("sqlite://").begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        inspector = inspect(connection)
        columns = {c["name"] for c in inspector.get_columns("auction_deals")}
        assert columns == set(AuctionDeal.__table__.columns.keys())
        assert {i["name"] for i in inspector.get_indexes("auction_deals")} == {"ix_auction_deals_found_at"}
        module.downgrade()
        assert "auction_deals" not in inspect(connection).get_table_names()


class _NullSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass
