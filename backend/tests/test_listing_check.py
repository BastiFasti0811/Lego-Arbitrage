"""Tages-Check (PR 3): Preistreppe, Marktabgleich, Schmerzgrenze, Snooze,
Verkaufs-Leichen, Sammelnachricht und die Anpassungs-Routen.

Regeln und Planung laufen DB-frei; Routen und Task gegen ein In-Memory-SQLite
mit den echten Modellen (Muster aus test_inventory_valuation_api.py), damit
WHERE-Klauseln, selectin-Ladung und naive SQLite-Zeitstempel mitgeprueft werden.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import listings as listing_routes
from app.api.routes.listings import (
    ListingUpdate,
    accept_suggestion,
    build_due_overview,
    dismiss_listing_suggestion,
    due_adjustments,
    snooze_listing_suggestion,
    update_listing,
)
from app.models import Base
from app.models.inventory import InventoryItem, InventoryStatus
from app.models.listing import Listing, ListingPriceChange
from app.notifications import telegram_bot
from app.notifications.delivery import NotificationDeliveryError
from app.notifications.telegram_bot import format_listing_check, send_listing_check
from app.services.heartbeat import MONITORED_TASKS
from app.services.listing_check import CheckEntry, CheckPlan, fresh_market_price, plan_listing_check
from app.services.listing_rules import (
    compute_suggestion,
    next_round_price_below,
    round_listing_price,
    staircase_steps,
)
from app.tasks import listing_check as listing_check_task
from app.tasks.celery_app import celery_app

NOW = datetime(2026, 10, 21, 6, 5, tzinfo=UTC)


# ── Rundung ───────────────────────────────────────────────────────────


def test_round_listing_price_uses_whole_euros_below_50_and_fives_above():
    assert round_listing_price(44.55) == 45.0
    assert round_listing_price(12.4) == 12.0
    assert round_listing_price(49.5) == 50.0  # kaufmaennisch, landet auf der 5er-Stufe
    assert round_listing_price(72.4) == 70.0
    assert round_listing_price(72.5) == 75.0
    assert round_listing_price(405.0) == 405.0


def test_next_round_price_below_is_strictly_lower():
    assert next_round_price_below(5.0) == 4.0
    assert next_round_price_below(5.5) == 5.0
    assert next_round_price_below(52.0) == 50.0
    assert next_round_price_below(50.0) == 49.0
    assert next_round_price_below(1.0) is None


# ── Preistreppe ───────────────────────────────────────────────────────


def test_staircase_steps_follow_listing_age():
    anchor = NOW - timedelta(days=14)
    assert staircase_steps(anchor, NOW, 14) == 1
    assert staircase_steps(NOW - timedelta(days=28), NOW, 14) == 2
    assert staircase_steps(NOW - timedelta(days=42), NOW, 14) == 3
    # Faellig, aber das Intervall wurde verlaengert: trotzdem eine Stufe.
    assert staircase_steps(NOW - timedelta(days=10), NOW, 14) == 1


def _suggest(current=50.0, floor=35.0, drop=10.0, steps=1, days=14, market=None):
    return compute_suggestion(
        current_price=current,
        min_price=floor,
        drop_percent=drop,
        steps=steps,
        days_unchanged=days,
        market_price=market,
    )


def test_first_step_drops_ten_percent_and_rounds():
    suggestion = _suggest(current=50.0)
    assert suggestion.price == 45.0
    assert "14 Tage ohne Preisänderung" in suggestion.reason
    assert "1 Stufe à 10 %" in suggestion.reason


def test_three_steps_compound_for_an_old_listing():
    # 120 x 0,9^3 = 87,48 -> 85 (ab 50 auf 5 EUR)
    suggestion = _suggest(current=120.0, floor=60.0, steps=3, days=42)
    assert suggestion.price == 85.0
    assert "3 Stufen" in suggestion.reason


def test_suggestion_never_goes_below_the_floor():
    suggestion = _suggest(current=40.0, floor=35.0, steps=3, days=42)
    assert suggestion.price == 35.0
    assert suggestion.clamped_to_floor
    assert "Schmerzgrenze 35 €" in suggestion.reason


def test_no_suggestion_at_the_floor():
    assert _suggest(current=35.0, floor=35.0) is None
    assert _suggest(current=30.0, floor=35.0) is None


def test_small_prices_still_get_a_lower_suggestion():
    # 5 x 0,9 = 4,5 -> kaufmaennisch 5 = kein Abschlag; also der naechste glatte Preis darunter.
    assert _suggest(current=5.0, floor=3.0).price == 4.0


def test_market_price_wins_when_clearly_below_the_staircase():
    suggestion = _suggest(current=100.0, floor=50.0, market=70.0)
    assert suggestion.price == 70.0
    assert "Marktpreis für diesen Zustand 70 €" in suggestion.reason
    assert "(90 €)" in suggestion.reason


def test_market_price_close_to_the_staircase_is_ignored():
    assert _suggest(current=100.0, floor=50.0, market=85.0).price == 90.0


def test_market_price_below_floor_is_clamped():
    suggestion = _suggest(current=100.0, floor=70.0, market=40.0)
    assert suggestion.price == 70.0
    assert suggestion.clamped_to_floor


def test_zero_drop_percent_only_follows_the_market():
    assert _suggest(drop=0.0) is None
    assert _suggest(current=100.0, floor=50.0, drop=0.0, market=60.0).price == 60.0


# ── Marktpreis-Anker ──────────────────────────────────────────────────


def _market_item(basis="CONSENSUS", price=100.0, age_days=2, condition="USED_COMPLETE"):
    return SimpleNamespace(
        market_price_basis=basis,
        current_market_price=price,
        market_price_updated_at=NOW - timedelta(days=age_days),
        condition=condition,
    )


def test_fresh_market_price_is_condition_adjusted():
    assert fresh_market_price(_market_item(), NOW) == 70.0  # gebraucht komplett = 70 %


def test_stale_or_brickmerge_only_market_price_is_not_used():
    assert fresh_market_price(_market_item(age_days=8), NOW) is None
    assert fresh_market_price(_market_item(basis="BRICKMERGE_ONLY"), NOW) is None
    assert fresh_market_price(_market_item(basis=None), NOW) is None


def test_naive_sqlite_timestamp_counts_as_utc():
    item = _market_item()
    item.market_price_updated_at = (NOW - timedelta(days=1)).replace(tzinfo=None)
    assert fresh_market_price(item, NOW) == 70.0


# ── Planung des Laufs (DB-frei) ───────────────────────────────────────


def _listing(
    listing_id=1,
    *,
    status="ACTIVE",
    price=50.0,
    floor=35.0,
    listed_days_ago=14,
    next_check_days_ago=0,
    suggested=None,
    snoozed_until=None,
    floor_notified_at=None,
    price_changes=(),
    url="https://www.kleinanzeigen.de/s-anzeige/x/123",
):
    return SimpleNamespace(
        id=listing_id,
        platform="KLEINANZEIGEN",
        status=status,
        url=url,
        current_price=price,
        min_price=floor,
        check_interval_days=14,
        price_drop_percent=10.0,
        listed_at=(NOW - timedelta(days=listed_days_ago)).date(),
        next_check_at=NOW - timedelta(days=next_check_days_ago),
        suggested_price=suggested,
        suggestion_reason="alt" if suggested else None,
        suggestion_at=NOW - timedelta(days=1) if suggested else None,
        snoozed_until=snoozed_until,
        floor_notified_at=floor_notified_at,
        price_changes=list(price_changes),
    )


def _item(item_id=1, *, status="HOLDING", listings=(), condition="NEW_SEALED"):
    return SimpleNamespace(
        id=item_id,
        status=status,
        set_number="75192",
        set_name="Millennium Falcon",
        condition=condition,
        listings=list(listings),
        market_price_basis=None,
        current_market_price=None,
        market_price_updated_at=None,
    )


def test_due_listing_gets_a_suggestion():
    listing = _listing()
    plan = plan_listing_check([_item(listings=[listing])], NOW)

    assert listing.suggested_price == 45.0
    assert listing.suggestion_at == NOW
    assert plan.checked == 1 and plan.new_suggestions == 1
    assert plan.suggestions[0].is_new
    assert plan.suggestions[0].label == "75192 Millennium Falcon"


def test_not_yet_due_listing_is_left_alone():
    listing = _listing(next_check_days_ago=-3)
    plan = plan_listing_check([_item(listings=[listing])], NOW)
    assert listing.suggested_price is None
    assert plan.checked == 0 and not plan.has_news


def test_staircase_counts_from_last_price_change():
    # Vor 42 Tagen eingestellt, vor 14 Tagen gesenkt: nur eine Stufe.
    change = SimpleNamespace(changed_at=NOW - timedelta(days=14))
    listing = _listing(price=45.0, listed_days_ago=42, price_changes=[change])
    plan_listing_check([_item(listings=[listing])], NOW)
    assert listing.suggested_price == 41.0  # 40,5 -> 41


def test_open_suggestion_is_repeated_but_not_recomputed():
    listing = _listing(suggested=45.0)
    plan = plan_listing_check([_item(listings=[listing])], NOW)
    assert listing.suggestion_reason == "alt"
    assert plan.new_suggestions == 0
    assert len(plan.suggestions) == 1 and not plan.suggestions[0].is_new


def test_snoozed_suggestion_stays_out_of_the_message():
    listing = _listing(suggested=45.0, snoozed_until=NOW + timedelta(days=2))
    plan = plan_listing_check([_item(listings=[listing])], NOW)
    assert plan.suggestions == [] and plan.snoozed == 1
    assert not plan.has_news


def test_expired_snooze_brings_the_suggestion_back():
    listing = _listing(suggested=45.0, snoozed_until=NOW - timedelta(minutes=1))
    plan = plan_listing_check([_item(listings=[listing])], NOW)
    assert len(plan.suggestions) == 1


def test_floor_hint_comes_exactly_once():
    listing = _listing(price=35.0, floor=35.0)
    item = _item(listings=[listing])

    first = plan_listing_check([item], NOW)
    second = plan_listing_check([item], NOW + timedelta(days=1))

    assert len(first.floor_hints) == 1 and listing.floor_notified_at == NOW
    assert second.floor_hints == [] and not second.has_news
    assert listing.suggested_price is None


def test_sold_item_with_running_listing_is_a_leftover():
    running = _listing(1, status="ACTIVE")
    paused = _listing(2, status="PAUSED")
    ended = _listing(3, status="ENDED")
    plan = plan_listing_check([_item(status="SOLD", listings=[running, paused, ended])], NOW)
    assert [e.listing_id for e in plan.sold_leftovers] == [1, 2]
    assert running.suggested_price is None  # verkaufte Posten bekommen keine Preisvorschlaege


def test_paused_and_draft_listings_get_no_suggestion():
    paused = _listing(1, status="PAUSED")
    draft = _listing(2, status="DRAFT")
    plan_listing_check([_item(listings=[paused, draft])], NOW)
    assert paused.suggested_price is None and draft.suggested_price is None


def test_used_item_follows_its_condition_adjusted_market_price():
    # Gebraucht, als OVP-Preis eingestellt: Konsens 100, gebraucht komplett = 70.
    listing = _listing(price=100.0, floor=50.0)
    item = _item(listings=[listing], condition="USED_COMPLETE")
    item.market_price_basis = "CONSENSUS"
    item.current_market_price = 100.0
    item.market_price_updated_at = NOW - timedelta(days=1)

    plan_listing_check([item], NOW)

    assert listing.suggested_price == 70.0
    assert "Marktpreis für diesen Zustand" in listing.suggestion_reason


# ── Sammelnachricht ───────────────────────────────────────────────────


def _entry(**overrides):
    base = dict(
        item_id=1,
        listing_id=1,
        label="75192 <Falcon>",
        platform="KLEINANZEIGEN",
        url="https://www.kleinanzeigen.de/s-anzeige/x/1?a=1&b=2",
        current_price=50.0,
        min_price=35.0,
        suggested_price=45.0,
        reason="14 Tage ohne Preisänderung",
        days_online=14,
        is_new=True,
    )
    base.update(overrides)
    return CheckEntry(**base)


def test_message_lists_suggestions_floor_and_leftovers_escaped():
    plan = CheckPlan(
        new_suggestions=1,
        suggestions=[_entry()],
        floor_hints=[_entry(listing_id=2, current_price=35.0, suggested_price=None)],
        sold_leftovers=[_entry(listing_id=3, platform="EBAY", url=None)],
        snoozed=2,
    )
    text = format_listing_check(plan)

    assert "1 Preisanpassung fällig" in text
    assert '<a href="https://www.kleinanzeigen.de/s-anzeige/x/1?a=1&amp;b=2">75192 &lt;Falcon&gt;</a>' in text
    assert "50 → <b>45 €</b>" in text
    assert "KA, 14 T online" in text
    assert "An der Schmerzgrenze" in text
    assert "Verkauft, Anzeige läuft noch" in text
    assert "eBay" in text
    assert "Zurückgestellt: 2." in text


def test_message_stays_under_telegram_limit():
    plan = CheckPlan(suggestions=[_entry(listing_id=i, reason="x" * 200) for i in range(60)])
    text = format_listing_check(plan)
    assert len(text) <= 4096
    assert "weitere in der App" in text


def test_non_http_url_is_not_linked():
    text = format_listing_check(CheckPlan(suggestions=[_entry(url="javascript:alert(1)")]))
    assert "<a href" not in text


class _FakeBot:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages: list[str] = []

    async def send_message(self, *, chat_id, text, parse_mode=None, disable_web_page_preview=None):
        if self.fail:
            raise RuntimeError("telegram down")
        self.messages.append(text)


@pytest.fixture
def fake_bot(monkeypatch):
    holder = {"bot": _FakeBot()}

    async def _settings(_keys):
        return {"telegram_bot_token": "t", "telegram_chat_id": "1"}

    monkeypatch.setattr(telegram_bot, "get_settings_map", _settings)
    monkeypatch.setattr(telegram_bot, "Bot", lambda token: holder["bot"])
    return holder


@pytest.mark.asyncio
async def test_send_listing_check_reports_delivery(fake_bot):
    assert await send_listing_check(CheckPlan(suggestions=[_entry()])) is True
    assert len(fake_bot["bot"].messages) == 1
    fake_bot["bot"] = _FakeBot(fail=True)
    assert await send_listing_check(CheckPlan(suggestions=[_entry()])) is False


# ── Beat-Plan ─────────────────────────────────────────────────────────


def test_listing_check_is_scheduled_daily_and_monitored():
    entry = celery_app.conf.beat_schedule["listing-check"]
    assert entry["task"] == "app.tasks.listing_check.run_listing_check_task"
    assert entry["schedule"].hour == {8}
    assert entry["schedule"].minute == {5}
    assert len(entry["schedule"].day_of_week) == 7
    assert MONITORED_TASKS["app.tasks.listing_check.run_listing_check_task"] == 26


# ── Echte Zeilen: In-Memory-SQLite ────────────────────────────────────


class _AsyncSessionAdapter:
    """Reicht Aufrufe an eine echte synchrone SQLAlchemy-Session durch."""

    def __init__(self, sync_session):
        self._session = sync_session
        self.commits = 0

    async def execute(self, statement):
        return self._session.execute(statement)

    def add(self, obj):
        self._session.add(obj)

    async def commit(self):
        self.commits += 1
        self._session.commit()

    async def refresh(self, obj):
        self._session.refresh(obj)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as sync_session:
        yield _AsyncSessionAdapter(sync_session)
    engine.dispose()


def _db_item(db, *, status=InventoryStatus.HOLDING.value, set_name="Millennium Falcon"):
    item = InventoryItem(
        set_number="75192",
        set_name=set_name,
        buy_price=100.0,
        buy_shipping=0.0,
        buy_date=date(2026, 1, 10),
        condition="NEW_SEALED",
        quantity=1,
        status=status,
    )
    db.add(item)
    db._session.flush()
    return item


def _db_listing(db, item, *, status="ACTIVE", price=50.0, floor=35.0, days_ago=14, platform="KLEINANZEIGEN"):
    listing = Listing(
        item_id=item.id,
        platform=platform,
        status=status,
        price_type="VB",
        listed_at=(datetime.now(UTC) - timedelta(days=days_ago)).date(),
        current_price=price,
        min_price=floor,
        url="https://www.kleinanzeigen.de/s-anzeige/x/1",
        next_check_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db.add(listing)
    db._session.flush()
    return listing


@pytest.mark.asyncio
async def test_task_writes_suggestions_and_sends_once(db, monkeypatch):
    item = _db_item(db)
    listing = _db_listing(db, item)
    floor_item = _db_item(db, set_name="Yoda")
    floor_listing = _db_listing(db, floor_item, price=35.0)
    await db.commit()

    sent_plans = []

    async def _send(plan):
        sent_plans.append(plan)
        return True

    monkeypatch.setattr(listing_check_task, "async_session", lambda: db)
    monkeypatch.setattr(listing_check_task, "send_listing_check", _send)

    result = await listing_check_task._check_async()

    db._session.expire_all()
    assert listing.suggested_price == 45.0
    assert floor_listing.floor_notified_at is not None
    assert result["new_suggestions"] == 1 and result["floor_hints"] == 1 and result["sent"] is True
    assert len(sent_plans) == 1


def test_task_without_news_sends_nothing_and_does_not_fail(db, monkeypatch):
    item = _db_item(db)
    listing = _db_listing(db, item)
    listing.next_check_at = datetime.now(UTC) + timedelta(days=3)
    db._session.commit()

    async def _send(plan):
        raise AssertionError("nichts zu melden, also keine Nachricht")

    monkeypatch.setattr(listing_check_task, "async_session", lambda: db)
    monkeypatch.setattr(listing_check_task, "send_listing_check", _send)
    monkeypatch.setattr(listing_check_task, "_run_async", lambda coro: _run(coro))

    result = listing_check_task.run_listing_check_task.run()

    assert result["needs_message"] is False and result["sent"] is False


def _run(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_undelivered_message_keeps_floor_hint_for_tomorrow(db, monkeypatch):
    item = _db_item(db)
    floor_listing = _db_listing(db, item, price=35.0)
    await db.commit()

    async def _send(plan):
        return False

    monkeypatch.setattr(listing_check_task, "async_session", lambda: db)
    monkeypatch.setattr(listing_check_task, "send_listing_check", _send)

    result = await listing_check_task._check_async()

    db._session.expire_all()
    assert floor_listing.floor_notified_at is None
    assert result["needs_message"] is True and result["sent"] is False
    with pytest.raises(NotificationDeliveryError):
        listing_check_task.require_delivery(result, "Listing-Tages-Check")


# ── Routen: Uebernommen / Spaeter / Verwerfen / Uebersicht ────────────


async def _with_suggestion(db, **kwargs):
    item = _db_item(db)
    listing = _db_listing(db, item, **kwargs)
    listing.suggested_price = 45.0
    listing.suggestion_reason = "14 Tage ohne Preisänderung"
    listing.suggestion_at = datetime.now(UTC)
    await db.commit()
    return item, listing


@pytest.mark.asyncio
async def test_accept_sets_price_writes_event_and_reschedules(db):
    item, listing = await _with_suggestion(db)

    response = await accept_suggestion(item.id, listing.id, session=db)

    assert response.current_price == 45.0
    assert response.suggested_price is None
    assert [(c.old_price, c.new_price) for c in response.price_changes] == [(50.0, 45.0)]
    assert response.next_check_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(days=13)


@pytest.mark.asyncio
async def test_snooze_keeps_the_suggestion_for_three_days(db):
    item, listing = await _with_suggestion(db)

    response = await snooze_listing_suggestion(item.id, listing.id, session=db)

    assert response.suggested_price == 45.0
    snoozed = response.snoozed_until.replace(tzinfo=UTC)
    assert timedelta(days=2, hours=23) < snoozed - datetime.now(UTC) <= timedelta(days=3)


@pytest.mark.asyncio
async def test_dismiss_clears_and_reschedules_without_price_change(db):
    item, listing = await _with_suggestion(db)

    response = await dismiss_listing_suggestion(item.id, listing.id, session=db)

    assert response.suggested_price is None and response.suggestion_reason is None
    assert response.current_price == 50.0
    assert response.price_changes == []
    assert response.next_check_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(days=13)


@pytest.mark.asyncio
async def test_suggestion_actions_need_an_open_suggestion(db):
    item = _db_item(db)
    listing = _db_listing(db, item)
    await db.commit()
    for action in (accept_suggestion, snooze_listing_suggestion, dismiss_listing_suggestion):
        with pytest.raises(HTTPException) as exc:
            await action(item.id, listing.id, session=db)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_manual_price_change_clears_open_suggestion(db):
    item, listing = await _with_suggestion(db)

    response = await update_listing(item.id, listing.id, ListingUpdate(current_price=48.0), session=db)

    assert response.current_price == 48.0
    assert response.suggested_price is None


@pytest.mark.asyncio
async def test_new_floor_resets_the_one_time_hint(db):
    item = _db_item(db)
    listing = _db_listing(db, item, price=35.0)
    listing.floor_notified_at = datetime.now(UTC)
    await db.commit()

    await update_listing(item.id, listing.id, ListingUpdate(min_price=30.0), session=db)

    db._session.expire_all()
    assert listing.floor_notified_at is None


@pytest.mark.asyncio
async def test_due_overview_groups_suggestions_floor_and_leftovers(db):
    _, open_listing = await _with_suggestion(db)
    snoozed_item = _db_item(db, set_name="Snoozed")
    snoozed = _db_listing(db, snoozed_item)
    snoozed.suggested_price = 40.0
    snoozed.snoozed_until = datetime.now(UTC) + timedelta(days=2)
    floor_item = _db_item(db, set_name="Am Boden")
    _db_listing(db, floor_item, price=35.0)
    sold_item = _db_item(db, status=InventoryStatus.SOLD.value, set_name="Weg")
    _db_listing(db, sold_item, platform="EBAY")
    other = _db_item(db, set_name="Ruhig")
    _db_listing(db, other, days_ago=2)
    await db.commit()

    overview = await due_adjustments(session=db)

    assert [e.listing.id for e in overview.suggestions] == [open_listing.id, snoozed.id]
    assert [e.snoozed for e in overview.suggestions] == [False, True]
    assert [e.item_label for e in overview.at_floor] == ["75192 Am Boden"]
    assert [e.item_label for e in overview.sold_leftovers] == ["75192 Weg"]
    assert overview.due_count == 2  # ein offener Vorschlag + eine Leiche
    assert overview.suggestions[0].days_online == 14


def test_overview_router_is_mounted_under_api_listings():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/listings/due" in paths
    assert "/api/inventory/{item_id}/listings/{listing_id}/suggestion/accept" in paths
    assert listing_routes.overview_router is not None


def test_build_due_overview_ignores_draft_items():
    draft_item = _item(status="DRAFT", listings=[_listing(suggested=40.0)])
    assert build_due_overview([draft_item], NOW).due_count == 0


def test_price_change_model_is_used_for_anchor():
    # Absicherung, dass die Preis-Historie am Modell haengt (selectin), sonst
    # rechnete die Treppe immer ab dem Einstell-Datum.
    assert Listing.price_changes.property.mapper.class_ is ListingPriceChange
