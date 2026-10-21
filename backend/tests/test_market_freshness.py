"""Veraltete Marktwerte: eine Altersregel, und jeder Leser haelt sich daran.

Messung Prod 21.10.2026 (Lauf #206): 40 von 49 Sets bewertet. Von den 9
uebersprungenen trugen 8 Marktwerte vom 20.08. bis 04.10., alle mit aktivem
Sell-Signal. 42055 zeigte "ROI erreicht" auf 600 EUR vom 20.08., BrickEconomy
meldete 250 EUR. Die Oberflaeche zeigte den Wert wie einen frischen.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.ai import ListingText
from app.api.routes.inventory import _to_response, portfolio_summary
from app.domain.market_freshness import MARKET_MAX_AGE_DAYS, is_market_price_stale
from app.services.listing_prepare import prepare_listing_draft, stale_market_warning
from app.tasks.update_inventory import _downgrade_stale_signal
from tests.test_inventory_optional_buy_price import _item as _response_item
from tests.test_inventory_optional_buy_price import _SummarySession

NOW = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
FRESH = NOW - timedelta(days=MARKET_MAX_AGE_DAYS - 1)
STALE = datetime(2026, 8, 20, 16, 30, tzinfo=UTC)


# ── Die Regel ─────────────────────────────────────────────────────────


def test_a_value_within_the_window_is_fresh():
    assert is_market_price_stale(FRESH, NOW) is False


def test_a_value_beyond_the_window_is_stale():
    assert is_market_price_stale(NOW - timedelta(days=MARKET_MAX_AGE_DAYS, minutes=1), NOW) is True


def test_a_value_without_timestamp_is_stale():
    # Niemand weiss, von wann er stammt.
    assert is_market_price_stale(None, NOW) is True


def test_naive_and_aware_timestamps_compare_as_utc():
    # Der Bewertungslauf rechnet mit naivem utcnow(), die DB liefert aware.
    naive_now = NOW.replace(tzinfo=None)
    assert is_market_price_stale(FRESH, naive_now) is False
    assert is_market_price_stale(STALE.replace(tzinfo=None), NOW) is True


# ── Bewertungslauf: Sell-Signal eines uebersprungenen Postens ─────────


def _skipped_item(**fields):
    defaults = dict(
        set_number="42055",
        buy_date=date(2026, 8, 1),
        current_market_price=600.0,
        market_price_updated_at=STALE,
        unrealized_roi_percent=150.0,
        sell_signal_active=True,
        sell_signal_reason="ROI 150% hat Zielwert 25% erreicht",
    )
    return SimpleNamespace(**{**defaults, **fields})


def test_a_stale_roi_signal_is_withdrawn():
    item = _skipped_item()

    _downgrade_stale_signal(item, NOW, release_year=2016)

    # Haltedauer (2,7 Monate) unter dem Ziel, ROI beruht auf dem alten Preis.
    assert item.sell_signal_active is False
    assert item.sell_signal_reason is None
    # Der Wert selbst bleibt als Anhaltspunkt stehen.
    assert item.current_market_price == 600.0


def test_a_stale_value_keeps_the_holding_time_signal_and_says_why():
    item = _skipped_item(buy_date=date(2022, 1, 1))

    _downgrade_stale_signal(item, NOW, release_year=2016)

    assert item.sell_signal_active is True
    assert item.sell_signal_reason.startswith("Optimale Haltedauer")
    assert "ROI" not in item.sell_signal_reason
    assert item.sell_signal_reason.endswith("(Marktwert vom 20.08.2026 veraltet)")


def test_a_fresh_value_keeps_its_signal_after_a_single_skip():
    # Ein einzelner Fehlversuch soll das Signal nicht flackern lassen.
    item = _skipped_item(market_price_updated_at=FRESH)

    _downgrade_stale_signal(item, NOW, release_year=2016)

    assert item.sell_signal_active is True
    assert item.sell_signal_reason == "ROI 150% hat Zielwert 25% erreicht"


def test_an_item_without_market_value_is_left_alone():
    item = _skipped_item(current_market_price=None, market_price_updated_at=None,
                         sell_signal_active=False, sell_signal_reason=None)

    _downgrade_stale_signal(item, NOW, release_year=None)

    assert item.sell_signal_active is False


# ── API ───────────────────────────────────────────────────────────────


def test_response_flags_a_stale_market_value():
    response = _to_response(_response_item(current_market_price=600.0, market_price_updated_at=STALE))
    assert response.market_price_stale is True


def test_response_does_not_flag_a_fresh_value():
    recent = datetime.now(UTC) - timedelta(hours=6)
    response = _to_response(_response_item(current_market_price=60.0, market_price_updated_at=recent))
    assert response.market_price_stale is False


def test_response_does_not_flag_a_missing_value():
    response = _to_response(_response_item(current_market_price=None, market_price_updated_at=None))
    assert response.market_price_stale is False


@pytest.mark.asyncio
async def test_summary_counts_stale_market_values_of_held_items_only():
    recent = datetime.now(UTC) - timedelta(hours=6)
    items = [
        _response_item(status="HOLDING", current_market_price=600.0, market_price_updated_at=STALE),
        _response_item(status="HOLDING", current_market_price=60.0, market_price_updated_at=recent),
        _response_item(status="HOLDING", current_market_price=None, market_price_updated_at=None),
        _response_item(status="SOLD", current_market_price=90.0, market_price_updated_at=STALE),
    ]

    summary = await portfolio_summary(session=_SummarySession(items))

    assert summary.stale_market_values == 1


# ── Inserat vorbereiten ───────────────────────────────────────────────


def _lego_item(**fields):
    defaults = dict(
        id=1, listings=[], status="HOLDING", item_type="LEGO", set_number="42055",
        set_name="Bucket Wheel Excavator", quantity=1, condition="NEW_SEALED", notes=None,
        current_market_price=600.0, market_price_basis="CONSENSUS", market_price_updated_at=STALE,
        ai_price_min=None, ai_price_max=None, buy_price=None,
    )
    return SimpleNamespace(**{**defaults, **fields})


class _Provider:
    async def write_listing(self, **_kwargs):
        return ListingText(title="LEGO 42055", body="Text", platform_category="")


def test_stale_market_warning_names_the_date():
    warning = stale_market_warning(_lego_item(), NOW)
    assert warning is not None
    assert "20.08.2026" in warning


def test_no_warning_for_a_fresh_market_value():
    assert stale_market_warning(_lego_item(market_price_updated_at=FRESH), NOW) is None


def test_no_warning_without_market_value():
    assert stale_market_warning(_lego_item(current_market_price=None), NOW) is None


@pytest.mark.asyncio
async def test_prepare_warns_when_the_suggested_price_comes_from_a_stale_value(monkeypatch):
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: _Provider())

    prepared = await prepare_listing_draft(_lego_item(), "KLEINANZEIGEN")

    assert prepared.listing.current_price == 600.0
    assert prepared.warning is not None
    assert "veralteten Marktwert" in prepared.warning


@pytest.mark.asyncio
async def test_prepare_does_not_warn_when_the_price_is_given(monkeypatch):
    # Wer den Preis selbst nennt, braucht keinen Hinweis auf den Marktwert.
    monkeypatch.setattr("app.services.listing_prepare.get_provider", lambda: _Provider())

    prepared = await prepare_listing_draft(_lego_item(), "KLEINANZEIGEN", price=280.0)

    assert prepared.warning is None
