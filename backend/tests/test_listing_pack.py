"""Montags-Paket, Zielzaehler und Refresh-Preis des Entwurfs — DB-frei."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.routes.inventory import lego_listing_counts, was_ever_listed
from app.notifications import telegram_bot
from app.notifications.telegram_bot import (
    format_listing_pack_entry,
    format_listing_pack_header,
    send_listing_pack,
)
from app.services.heartbeat import MONITORED_TASKS
from app.tasks import listing_pack
from app.tasks.celery_app import celery_app
from app.tasks.listing_pack import PackEntry, is_unlisted, pick_pack_candidates


def _listing(status, listed_at=None, platform="KLEINANZEIGEN"):
    return SimpleNamespace(status=status, listed_at=listed_at, platform=platform)


def _item(item_id=1, *, status="HOLDING", item_type="LEGO", listings=(), photos=(), market=None, buy=None):
    return SimpleNamespace(
        id=item_id,
        status=status,
        item_type=item_type,
        listings=list(listings),
        photos=list(photos),
        current_market_price=market,
        ai_price_min=None,
        ai_price_max=None,
        buy_price=buy,
    )


# ── Zielzaehler ───────────────────────────────────────────────────────


def test_ever_listed_counts_ended_and_sold_listings_but_not_drafts():
    assert was_ever_listed(_item(listings=[_listing("ENDED", date(2026, 9, 1))]))
    assert was_ever_listed(_item(listings=[_listing("SOLD", date(2026, 9, 1))]))
    assert not was_ever_listed(_item(listings=[_listing("DRAFT")]))
    assert not was_ever_listed(_item())


def test_lego_counts_ignore_generic_items():
    items = [
        _item(1, listings=[_listing("ACTIVE", date(2026, 10, 1))]),
        _item(2, status="SOLD", listings=[_listing("SOLD", date(2026, 9, 1))]),
        _item(3, status="SOLD"),  # privat verkauft, nie inseriert
        _item(4, listings=[_listing("DRAFT")]),
        _item(5, item_type="GENERIC", listings=[_listing("ACTIVE", date(2026, 10, 1))]),
    ]

    assert lego_listing_counts(items) == {"lego_items": 4, "lego_listed": 2, "lego_sold": 2}


# ── Auswahl fuers Montags-Paket ───────────────────────────────────────


def test_draft_only_item_still_counts_as_unlisted():
    assert is_unlisted(_item(listings=[_listing("DRAFT")]))
    assert is_unlisted(_item(listings=[_listing("ENDED", date(2026, 9, 1))]))
    assert not is_unlisted(_item(listings=[_listing("PAUSED", date(2026, 9, 1))]))
    assert not is_unlisted(_item(listings=[_listing("ACTIVE", date(2026, 9, 1), platform="EBAY")]))


def test_pick_prefers_photos_then_highest_price_and_skips_ineligible():
    photo = SimpleNamespace(filename="a.jpg")
    items = [
        _item(1, market=300.0),  # teuer, aber ohne Foto
        _item(2, market=50.0, photos=[photo]),
        _item(3, market=120.0, photos=[photo]),
        _item(4, market=999.0, photos=[photo], listings=[_listing("ACTIVE", date(2026, 10, 1))]),
        _item(5, market=999.0, photos=[photo], item_type="GENERIC"),
        _item(6, market=999.0, photos=[photo], status="SOLD"),
        _item(7, photos=[photo]),  # kein Preisvorschlag ermittelbar
        _item(8, buy=10.0, photos=[photo]),  # 1,5 x Einkauf = 15
    ]

    picked = pick_pack_candidates(items, limit=3)

    assert [i.id for i in picked] == [3, 2, 8]


def test_pick_respects_limit_of_five_by_default():
    items = [_item(i, market=float(i)) for i in range(1, 9)]

    assert len(pick_pack_candidates(items)) == 5


def test_pack_task_is_scheduled_monday_morning_and_monitored():
    entry = celery_app.conf.beat_schedule["listing-pack"]
    assert entry["task"] == "app.tasks.listing_pack.send_listing_pack_task"
    assert entry["schedule"].day_of_week == {1}
    assert entry["schedule"].hour == {8}
    assert "app.tasks.listing_pack.send_listing_pack_task" in MONITORED_TASKS


# ── Telegram-Text ─────────────────────────────────────────────────────


def _entry(**overrides):
    base = dict(
        item_id=1,
        label="75192 Millennium <Falcon>",
        title="LEGO 75192 Millennium Falcon",
        body="Toller Zustand & komplett.\nPreis: 450€ VB",
        price=450.0,
        price_type="VB",
        text_source="KI",
        storage_location="Kiste 3",
        photo_paths=[],
    )
    base.update(overrides)
    return PackEntry(**base)


def test_entry_escapes_html_and_offers_copyable_blocks():
    text = format_listing_pack_entry(1, 5, _entry())

    assert "<b>1/5 · 75192 Millennium &lt;Falcon&gt;</b>" in text
    assert "450 € VB" in text
    assert "📦 Kiste 3" in text
    assert "⚠️ keine Fotos" in text
    assert "<code>LEGO 75192 Millennium Falcon</code>" in text
    assert "<pre>Toller Zustand &amp; komplett.\nPreis: 450€ VB</pre>" in text


def test_entry_flags_template_text_and_counts_photos():
    text = format_listing_pack_entry(2, 5, _entry(text_source="VORLAGE", photo_paths=[Path("a"), Path("b")]))

    assert "2 Fotos" in text
    assert "Vorlagentext" in text


def test_entry_stays_under_telegram_limit_for_huge_body():
    text = format_listing_pack_entry(1, 1, _entry(body="Wort " * 2000))

    assert len(text) <= 4096
    assert text.endswith("…</pre>")


def test_header_without_candidates_says_so():
    assert "Kein Posten bereit" in format_listing_pack_header(0, 12)
    assert "5 Inserate der Woche" in format_listing_pack_header(5, 19)


class _FakeBot:
    def __init__(self, *, fail_photos=False, fail_texts=False):
        self.messages: list[str] = []
        self.media_groups: list[int] = []
        self.fail_photos = fail_photos
        self.fail_texts = fail_texts

    async def send_message(self, *, chat_id, text, parse_mode=None):
        if self.fail_texts and self.messages:
            raise RuntimeError("telegram down")
        self.messages.append(text)

    async def send_media_group(self, *, chat_id, media):
        if self.fail_photos:
            raise RuntimeError("photo too big")
        self.media_groups.append(len(media))


@pytest.fixture
def fake_bot(monkeypatch):
    holder = {}

    async def _settings(_keys):
        return {"telegram_bot_token": "t", "telegram_chat_id": "1"}

    def _bot(token):
        return holder["bot"]

    monkeypatch.setattr(telegram_bot, "get_settings_map", _settings)
    monkeypatch.setattr(telegram_bot, "Bot", _bot)
    return holder


@pytest.mark.asyncio
async def test_send_pack_sends_header_photos_and_texts(fake_bot, tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"\xff\xd8fake")
    fake_bot["bot"] = bot = _FakeBot()

    sent = await send_listing_pack([_entry(photo_paths=[photo]), _entry(item_id=2)], unlisted_total=7)

    assert sent is True
    assert len(bot.messages) == 3  # Kopf + zwei Posten
    assert bot.media_groups == [1]


@pytest.mark.asyncio
async def test_photo_failure_does_not_count_as_undelivered(fake_bot, tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    fake_bot["bot"] = _FakeBot(fail_photos=True)

    assert await send_listing_pack([_entry(photo_paths=[photo])], unlisted_total=1) is True


@pytest.mark.asyncio
async def test_lost_entry_text_counts_as_undelivered(fake_bot):
    fake_bot["bot"] = _FakeBot(fail_texts=True)

    assert await send_listing_pack([_entry()], unlisted_total=1) is False


@pytest.mark.asyncio
async def test_missing_credentials_is_not_delivered(monkeypatch):
    async def _settings(_keys):
        return {}

    monkeypatch.setattr(telegram_bot, "get_settings_map", _settings)

    assert await send_listing_pack([_entry()], unlisted_total=1) is False


def test_photo_paths_skip_missing_files(monkeypatch, tmp_path):
    monkeypatch.setattr(listing_pack, "PHOTO_STORAGE_ROOT", tmp_path)
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "da.jpg").write_bytes(b"x")
    item = _item(photos=[SimpleNamespace(filename="da.jpg"), SimpleNamespace(filename="weg.jpg")])

    assert listing_pack._photo_paths(item) == [tmp_path / "1" / "da.jpg"]
