from datetime import datetime
from types import SimpleNamespace

import pytest

from app.api.routes.inventory import _hydrate_market_snapshot, _recalculate_unrealized_metrics


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, *rows):
        self._rows = list(rows)

    async def execute(self, _query):
        return _FakeResult(self._rows.pop(0) if self._rows else None)


def _inventory_item():
    return SimpleNamespace(
        set_number="75331",
        buy_price=100.0,
        buy_shipping=5.0,
        current_market_price=None,
        market_price_updated_at=None,
        unrealized_profit=None,
        unrealized_roi_percent=None,
    )


@pytest.mark.asyncio
async def test_hydrate_market_snapshot_prefers_cached_set_price():
    item = _inventory_item()
    updated_at = datetime(2026, 3, 31, 12, 0, 0)
    session = _FakeSession((149.99, updated_at))

    hydrated = await _hydrate_market_snapshot(item, session)

    assert hydrated is True
    assert item.current_market_price == 149.99
    assert item.market_price_updated_at == updated_at
    assert item.unrealized_profit == 44.99
    assert item.unrealized_roi_percent == 42.8


@pytest.mark.asyncio
async def test_hydrate_market_snapshot_falls_back_to_latest_analysis():
    item = _inventory_item()
    analyzed_at = datetime(2026, 3, 30, 8, 30, 0)
    session = _FakeSession(None, (132.49, analyzed_at))

    hydrated = await _hydrate_market_snapshot(item, session)

    assert hydrated is True
    assert item.current_market_price == 132.49
    assert item.market_price_updated_at == analyzed_at
    assert item.unrealized_profit == 27.49
    assert item.unrealized_roi_percent == 26.2


def test_recalculate_unrealized_metrics_clears_values_without_market_price():
    item = _inventory_item()
    item.current_market_price = None
    item.unrealized_profit = 1
    item.unrealized_roi_percent = 2

    _recalculate_unrealized_metrics(item)

    assert item.unrealized_profit is None
    assert item.unrealized_roi_percent is None
