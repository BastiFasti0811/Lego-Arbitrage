"""Deal-Alert: Knöpfe "Gekauft"/"Abgelehnt", Deep-Link in den Checker.

Vorher trug der Alert "Details" und "Watchlist" mit callback_data, aber kein
Prozess holte die Klicks ab (Anwender B1/B2): die Knöpfe drehten nur den
Ladekreisel. Diese Tests halten fest, dass jeder Knopf jetzt etwas tut, dass
die Entscheidung gespeichert wird und dass der Wochen-Digest sie zählt.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from app.models import Base, DismissedOffer, LegoSet, Offer
from app.models.dismissal import dismissal_key
from app.models.offer_decision import DecisionKind, OfferDecision
from app.notifications import deal_actions, telegram_bot, telegram_callbacks
from app.notifications.telegram_bot import format_goal_digest
from app.services import offer_decisions
from app.services.goal_progress import build_digest, count_deals, week_of

NOW = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
URL = "https://www.kleinanzeigen.de/s-anzeige/lego-42143/123456789-23-1234"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "c2e7d4a91b58_offer_decisions.py"


# ── Knöpfe ────────────────────────────────────────────────────────────


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_alert_has_no_dead_buttons_left():
    markup = deal_actions.deal_keyboard(17, URL, "http://victus/lego/checker?offer=17")

    data = [b.callback_data for b in _buttons(markup) if b.callback_data]
    assert data == ["dec:17:b", "dec:17:r"]
    assert all(deal_actions.parse_callback(d) is not None for d in data), "jeder callback-Knopf muss verstanden werden"
    assert [b.url for b in _buttons(markup) if b.url] == [URL, "http://victus/lego/checker?offer=17"]


def test_callback_data_fits_telegrams_64_byte_limit():
    assert len(deal_actions.callback_data(2**31 - 1, "u").encode()) <= 64


def test_without_offer_row_there_is_nothing_to_decide():
    markup = deal_actions.deal_keyboard(None, URL, None)
    assert all(b.callback_data is None for b in _buttons(markup))


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://victus/lego", "http://victus/lego/checker?offer=5"),
        ("https://lego.example.org/lego/", "https://lego.example.org/lego/checker?offer=5"),
        ("  http://100.64.1.2/lego  ", "http://100.64.1.2/lego/checker?offer=5"),
        ("victus/lego", None),
        ("javascript:alert(1)", None),
        ("", None),
        (None, None),
    ],
)
def test_checker_link(base, expected):
    assert deal_actions.checker_link(base, 5) == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("dec:17:b", (17, DecisionKind.BOUGHT)),
        ("dec:17:r", (17, DecisionKind.REJECTED)),
        ("dec:17:u", (17, None)),
        ("dec:17:x", None),
        ("dec:abc:b", None),
        ("dec:-1:b", None),
        ("dec:17", None),
        ("detail_42143", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_callback(data, expected):
    parsed = deal_actions.parse_callback(data)
    assert (None if parsed is None else (parsed.offer_id, parsed.decision)) == expected


def test_decided_keyboard_keeps_links_and_offers_undo():
    sent = deal_actions.deal_keyboard(17, URL, "http://victus/lego/checker?offer=17")

    decided = deal_actions.with_decision(sent, 17, DecisionKind.BOUGHT.value)

    assert decided.inline_keyboard[0] == sent.inline_keyboard[0]
    last = decided.inline_keyboard[-1]
    assert [b.callback_data for b in last] == ["dec:17:u"]
    assert "Gekauft" in last[0].text

    undone = deal_actions.with_decision(decided, 17, None)
    assert [b.callback_data for b in undone.inline_keyboard[-1]] == ["dec:17:b", "dec:17:r"]
    assert len(undone.inline_keyboard) == 2


# ── Versand ───────────────────────────────────────────────────────────


class _FakeBot:
    def __init__(self, reject_first=False):
        self.sent = []
        self._reject_first = reject_first

    async def send_message(self, **kwargs):
        if self._reject_first and not self.sent:
            self.sent.append(kwargs)
            raise BadRequest("Wrong http url")
        self.sent.append(kwargs)


def _analysis():
    return SimpleNamespace(
        recommendation="GO",
        set_number="42143",
        set_name="Ferrari Daytona SP3",
        release_year=2022,
        uvp=399.99,
        market_consensus=SimpleNamespace(consensus_price=380.0, num_sources=3, warnings=[]),
        offer_price=250.0,
        discount_vs_uvp=37.5,
        roi=SimpleNamespace(
            total_purchase_cost=255.0, net_revenue=330.0, net_profit=75.0, roi_percent=29.4, annualized_roi=29.4
        ),
        set_age=4,
        category="RETIRING",
        risk=SimpleNamespace(color="green", total=3, rating="niedrig"),
        reason="Marktpreis deutlich ueber Angebot",
        suggestions=[],
    )


def _patch_send(monkeypatch, bot, public_url="http://victus/lego"):
    async def fake_settings(_keys):
        return {
            "telegram_bot_token": "123:abc",
            "telegram_chat_id": "42",
            "telegram_alert_on_go_only": "true",
            "app_public_url": public_url,
        }

    monkeypatch.setattr(telegram_bot, "get_settings_map", fake_settings)
    monkeypatch.setattr(telegram_bot, "Bot", lambda token: bot)


@pytest.mark.asyncio
async def test_alert_carries_deep_link_and_decision_buttons(monkeypatch):
    bot = _FakeBot()
    _patch_send(monkeypatch, bot)

    assert await telegram_bot.send_deal_alert(_analysis(), offer_url=URL, offer_id=17) is True

    buttons = _buttons(bot.sent[0]["reply_markup"])
    assert "http://victus/lego/checker?offer=17" in [b.url for b in buttons]
    assert {b.callback_data for b in buttons if b.callback_data} == {"dec:17:b", "dec:17:r"}


@pytest.mark.asyncio
async def test_rejected_deep_link_does_not_cost_the_alert(monkeypatch):
    bot = _FakeBot(reject_first=True)
    _patch_send(monkeypatch, bot)

    assert await telegram_bot.send_deal_alert(_analysis(), offer_url=URL, offer_id=17) is True

    retry = _buttons(bot.sent[-1]["reply_markup"])
    assert not any(b.url and "checker" in b.url for b in retry)
    assert {b.callback_data for b in retry if b.callback_data} == {"dec:17:b", "dec:17:r"}


@pytest.mark.asyncio
async def test_without_public_url_there_is_no_checker_button(monkeypatch):
    bot = _FakeBot()
    _patch_send(monkeypatch, bot, public_url=None)

    await telegram_bot.send_deal_alert(_analysis(), offer_url=URL, offer_id=17)

    assert [b.url for b in _buttons(bot.sent[0]["reply_markup"]) if b.url] == [URL]


# ── Entscheidung speichern (echte SQLite-Session) ─────────────────────


class _AsyncSession:
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


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lego_set = LegoSet(set_number="42143", set_name="Ferrari Daytona SP3", release_year=2022, theme="Technic")
        session.add(lego_set)
        session.flush()
        offer = Offer(
            set_id=lego_set.id,
            platform="KLEINANZEIGEN",
            offer_url=URL,
            offer_title="LEGO 42143 neu OVP",
            price_eur=250.0,
            discovered_at=NOW,
            last_seen_at=NOW,
            recommendation="GO",
        )
        session.add(offer)
        session.commit()
        yield SimpleNamespace(session=session, adapter=_AsyncSession(session), offer_id=offer.id)
    engine.dispose()


def _decisions(db):
    return db.session.execute(select(OfferDecision)).scalars().all()


def _dismissed(db):
    return db.session.execute(select(DismissedOffer)).scalars().all()


@pytest.mark.asyncio
async def test_bought_is_stored_once_even_when_pressed_twice(db):
    first = await offer_decisions.record_decision(db.adapter, db.offer_id, DecisionKind.BOUGHT, NOW)
    second = await offer_decisions.record_decision(db.adapter, db.offer_id, DecisionKind.BOUGHT, NOW)

    assert (first.decision, first.previous) == ("BOUGHT", None)
    assert second.previous == "BOUGHT"
    [stored] = _decisions(db)
    assert (stored.decision, stored.source, stored.set_number) == ("BOUGHT", "TELEGRAM", "42143")
    assert (stored.price_eur, stored.recommendation) == (250.0, "GO")
    assert _dismissed(db) == []


@pytest.mark.asyncio
async def test_rejected_leaves_the_feed_and_undo_brings_it_back(db):
    await offer_decisions.record_decision(db.adapter, db.offer_id, DecisionKind.REJECTED, NOW)

    [dismissed] = _dismissed(db)
    # Derselbe Schlüssel wie das ✕ im Live Feed, sonst bliebe das Inserat sichtbar.
    assert dismissed.offer_identity == dismissal_key("KLEINANZEIGEN", URL)

    undone = await offer_decisions.undo_decision(db.adapter, db.offer_id)

    assert (undone.decision, undone.previous) == (None, "REJECTED")
    assert _decisions(db) == []
    assert _dismissed(db) == []


@pytest.mark.asyncio
async def test_switching_from_rejected_to_bought_releases_the_dismissal(db):
    await offer_decisions.record_decision(db.adapter, db.offer_id, DecisionKind.REJECTED, NOW)
    later = NOW + timedelta(hours=1)

    outcome = await offer_decisions.record_decision(db.adapter, db.offer_id, DecisionKind.BOUGHT, later)

    assert outcome.previous == "REJECTED"
    [stored] = _decisions(db)
    assert stored.decision == "BOUGHT"
    assert stored.decided_at.replace(tzinfo=UTC) == later
    assert _dismissed(db) == []


@pytest.mark.asyncio
async def test_a_vanished_offer_is_reported_not_crashed(db):
    outcome = await offer_decisions.record_decision(db.adapter, 99999, DecisionKind.BOUGHT, NOW)
    assert outcome.found is False


# ── Klick verarbeiten ─────────────────────────────────────────────────


class _ClickBot:
    def __init__(self):
        self.answers = []
        self.edits = []

    async def answer_callback_query(self, query_id, text=None):
        self.answers.append(text)

    async def edit_message_reply_markup(self, **kwargs):
        self.edits.append(kwargs)


def _query(data, chat_id=42, username=None):
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 Angebot öffnen", url=URL)],
            deal_actions.decision_row(17, None),
        ]
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, username=username), message_id=5, reply_markup=markup
    )
    return SimpleNamespace(id="q1", data=data, message=message)


@pytest.fixture
def recorded(monkeypatch):
    calls = []

    class _Ctx:
        async def __aenter__(self):
            return "session"

        async def __aexit__(self, *_exc):
            return False

    async def fake_record(session, offer_id, decision, now):
        calls.append((offer_id, decision))
        return offer_decisions.DecisionOutcome(found=True, decision=decision.value, set_number="42143")

    async def fake_undo(session, offer_id):
        calls.append((offer_id, None))
        return offer_decisions.DecisionOutcome(found=True, decision=None, previous="BOUGHT", set_number="42143")

    monkeypatch.setattr(telegram_callbacks, "async_session", _Ctx)
    monkeypatch.setattr(telegram_callbacks, "record_decision", fake_record)
    monkeypatch.setattr(telegram_callbacks, "undo_decision", fake_undo)
    return calls


@pytest.mark.asyncio
async def test_click_saves_and_swaps_the_buttons(recorded):
    bot = _ClickBot()

    text = await telegram_callbacks.handle_callback(bot, _query("dec:17:b"), "42", now=NOW)

    assert recorded == [(17, DecisionKind.BOUGHT)]
    assert "gekauft" in text
    assert bot.answers == [text]
    new_rows = bot.edits[0]["reply_markup"].inline_keyboard
    assert new_rows[0][0].url == URL
    assert [b.callback_data for b in new_rows[-1]] == ["dec:17:u"]


@pytest.mark.asyncio
async def test_click_from_another_chat_changes_nothing(recorded):
    bot = _ClickBot()

    text = await telegram_callbacks.handle_callback(bot, _query("dec:17:r", chat_id=666), "42")

    assert recorded == []
    assert text == telegram_callbacks.FOREIGN_CHAT_ANSWER
    assert bot.edits == []


@pytest.mark.asyncio
async def test_chat_configured_as_at_name_is_accepted(recorded):
    await telegram_callbacks.handle_callback(_ClickBot(), _query("dec:17:r", username="SebsDeals"), "@sebsdeals")
    assert recorded == [(17, DecisionKind.REJECTED)]


@pytest.mark.asyncio
async def test_old_details_button_gets_an_answer_instead_of_a_spinner(recorded):
    bot = _ClickBot()

    await telegram_callbacks.handle_callback(bot, _query("detail_42143"), "42")

    assert recorded == []
    assert bot.answers == [telegram_callbacks.LEGACY_ANSWER]


@pytest.mark.asyncio
async def test_undo(recorded):
    bot = _ClickBot()

    text = await telegram_callbacks.handle_callback(bot, _query("dec:17:u"), "42")

    assert recorded == [(17, None)]
    assert "zurückgenommen" in text
    assert [b.callback_data for b in bot.edits[0]["reply_markup"].inline_keyboard[-1]] == ["dec:17:b", "dec:17:r"]


# ── Wochen-Digest ─────────────────────────────────────────────────────


def _decision(kind, at):
    return SimpleNamespace(decision=kind, decided_at=at)


def test_decisions_are_counted_in_the_week_they_were_made():
    week = week_of(NOW)
    decisions = [
        _decision("BOUGHT", NOW),
        _decision("REJECTED", NOW - timedelta(days=1)),
        _decision("REJECTED", NOW - timedelta(days=1, hours=2)),
        _decision("BOUGHT", week.start - timedelta(minutes=1)),  # Vorwoche
    ]

    count = count_deals([], [], week, decisions)

    assert (count.bought, count.rejected) == (1, 2)
    assert count.total == 0, "Entscheidungen sind keine zusätzlichen Deals"


def test_digest_names_the_decisions():
    monday = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    decisions = [_decision("BOUGHT", NOW), _decision("REJECTED", NOW)]

    text = format_goal_digest(build_digest([], [], [], monday, decisions))

    assert "gekauft 1 · abgelehnt 1" in text


def test_digest_stays_quiet_without_decisions():
    monday = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    assert "Entschieden" not in format_goal_digest(build_digest([], [], [], monday))


# ── Migration passt zum Modell ────────────────────────────────────────


def test_migration_builds_the_table_the_model_expects():
    spec = importlib.util.spec_from_file_location("migration_c2e7d4a91b58", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        created = {c["name"] for c in inspect(conn).get_columns("offer_decisions")}
        indexes = {i["name"]: i["unique"] for i in inspect(conn).get_indexes("offer_decisions")}

    assert {c.name for c in OfferDecision.__table__.columns} - created == set()
    assert indexes["ix_offer_decisions_offer_identity"] == 1


# ── Abholschleife ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poller_without_token_waits_and_stops_cleanly(monkeypatch):
    import asyncio

    async def no_token(_keys):
        return {"telegram_bot_token": None}

    monkeypatch.setattr(telegram_callbacks, "get_settings_map", no_token)
    stop = asyncio.Event()
    task = asyncio.create_task(telegram_callbacks.poll_callbacks(stop))
    await asyncio.sleep(0.05)
    stop.set()

    await asyncio.wait_for(task, timeout=1)


class _SendBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "answered"), [("/id", True), ("/start", True), ("/id@LegoBot", True), ("hallo", False)]
)
async def test_bot_tells_the_chat_its_id(text, answered):
    # Solange die API abholt, liefert ein händisches getUpdates nur 409. Das
    # Runbook für die Chat-ID führt deshalb über /id.
    bot = _SendBot()
    message = SimpleNamespace(text=text, chat=SimpleNamespace(id=-100123))

    assert await telegram_callbacks.answer_chat_id(bot, message) is answered
    assert bool(bot.sent) is answered
    if answered:
        assert bot.sent[0]["chat_id"] == -100123
        assert "-100123" in bot.sent[0]["text"]
