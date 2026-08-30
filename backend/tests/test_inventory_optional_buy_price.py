"""Dachbodenfunde haben keinen Kaufpreis (Spec PR 1, Grill-Entscheid Q4):
ehrlich fehlend schlaegt falsch berechnet — nirgends 0 als Ersatzwert."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.api.routes.inventory import (
    SellRequest,
    _recalculate_unrealized_metrics,
    _to_response,
    get_sell_links,
    mark_as_sold,
    portfolio_summary,
)


def _item(**overrides):
    base = dict(
        id=1,
        set_number=None,
        set_name="Bohrmaschine",
        theme=None,
        image_url=None,
        item_type="GENERIC",
        product_group="Elektronik",
        search_query="Bosch PSB 500",
        buy_price=None,
        buy_shipping=0.0,
        buy_date=date(2026, 8, 1),
        buy_platform=None,
        buy_url=None,
        condition="USED_COMPLETE",
        quantity=1,
        notes=None,
        photos=[],
        listings=[],
        current_market_price=None,
        market_price_updated_at=None,
        unrealized_profit=None,
        unrealized_roi_percent=None,
        sell_signal_active=False,
        sell_signal_reason=None,
        status="HOLDING",
        sell_price=None,
        sell_date=None,
        sell_platform=None,
        realized_profit=None,
        realized_roi_percent=None,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_response_has_no_invented_invest_without_buy_price():
    response = _to_response(_item())
    assert response.buy_price is None
    assert response.total_invested is None


def test_response_still_computes_invest_with_buy_price():
    response = _to_response(_item(buy_price=100.0, buy_shipping=5.0))
    assert response.total_invested == 105.0


def test_unrealized_metrics_skip_items_without_buy_price():
    item = _item(current_market_price=80.0)
    _recalculate_unrealized_metrics(item)
    assert item.unrealized_profit is None
    assert item.unrealized_roi_percent is None


class _ScalarsWrapper:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteAllResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarsWrapper(self._items)


class _SummarySession:
    """Fake session for portfolio_summary: one execute() returning all items."""

    def __init__(self, items):
        self._items = items

    async def execute(self, _query):
        return _ExecuteAllResult(self._items)


@pytest.mark.asyncio
async def test_portfolio_summary_mixes_priced_and_unpriced():
    item_a = _item(status="HOLDING", buy_price=100.0, buy_shipping=5.0, current_market_price=150.0)
    item_b = _item(status="HOLDING", buy_price=None, buy_shipping=0.0, current_market_price=80.0)
    item_c = _item(status="HOLDING", buy_price=50.0, buy_shipping=0.0, current_market_price=None)
    session = _SummarySession([item_a, item_b, item_c])

    summary = await portfolio_summary(session=session)

    # A: (150 - 105) = 45 unrealized; B has no buy_price so its 80 market value must not
    # count as profit; C has no market price so it contributes 0 unrealized.
    assert summary.total_invested == 155.0
    assert summary.current_value == 280.0
    assert summary.unrealized_profit == 45.0
    assert summary.holding_items == 3
    assert summary.sold_items == 0


class _ScalarOneResult:
    def __init__(self, item):
        self._item = item

    def scalar_one_or_none(self):
        return self._item


class _MarkAsSoldSession:
    """Fake session for mark_as_sold: one execute() for _get_item, then commit/refresh/add."""

    def __init__(self, item):
        self._item = item
        self.added: list = []

    async def execute(self, _query):
        return _ScalarOneResult(self._item)

    async def commit(self):
        pass

    async def refresh(self, _item):
        pass

    def add(self, obj):
        self.added.append(obj)


@pytest.mark.asyncio
async def test_mark_as_sold_without_buy_price_sets_no_profit():
    item = _item(buy_price=None, status="HOLDING")
    session = _MarkAsSoldSession(item)
    data = SellRequest(sell_price=42.0)

    result = await mark_as_sold(item_id=1, data=data, session=session)

    assert result.status == "SOLD"
    assert result.realized_profit is None
    assert result.realized_roi_percent is None
    assert session.added == []


class _GetItemSession:
    """Fake session for get_sell_links: session.get() returns the item directly."""

    def __init__(self, item):
        self._item = item

    async def get(self, _model, _item_id):
        return self._item


@pytest.mark.asyncio
async def test_get_sell_links_generic_without_prices():
    item = _item(
        set_number=None,
        set_name="Bohrmaschine",
        buy_price=None,
        current_market_price=None,
        condition="USED_COMPLETE",
    )
    session = _GetItemSession(item)

    response = await get_sell_links(item_id=1, session=session)

    assert response.suggested_price == 0.0
    assert "None" not in response.ebay_title
    assert "Bohrmaschine" in response.kleinanzeigen_text
