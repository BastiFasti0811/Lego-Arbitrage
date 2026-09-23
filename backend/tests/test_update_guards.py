"""Handler guards for update_inventory_item (PR-1-Reste)."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes.inventory import InventoryUpdate, update_inventory_item


def _fake_item(**overrides):
    """Factory for InventoryItem SimpleNamespace with ai_ fields from Task 2."""
    base = dict(
        id=1,
        set_number=None,
        set_name="Testartikel",
        theme=None,
        image_url=None,
        item_type="GENERIC",
        product_group="Diverses",
        search_query="Test Search",
        ai_price_min=None,
        ai_price_max=None,
        ai_analysis_at=None,
        buy_price=None,
        buy_shipping=0.0,
        buy_date=date(2026, 8, 1),
        buy_platform=None,
        buy_url=None,
        reference_url=None,
        condition="USED_COMPLETE",
        quantity=1,
        notes=None,
        storage_location=None,
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


class _ScalarOneResult:
    """Wraps a single item for session.execute() -> scalar_one_or_none()."""

    def __init__(self, item):
        self._item = item

    def scalar_one_or_none(self):
        return self._item


class _Session:
    """Fake session for update_inventory_item: one execute() for _get_item, then commit/refresh."""

    def __init__(self, item):
        self._item = item

    async def execute(self, _query):
        return _ScalarOneResult(self._item)

    async def commit(self):
        pass

    async def refresh(self, _item):
        pass


@pytest.mark.asyncio
async def test_update_rejects_product_group_change_on_lego():
    item = _fake_item(item_type="LEGO", set_number="75331", product_group="Lego")
    with pytest.raises(HTTPException) as exc:
        await update_inventory_item(1, InventoryUpdate(product_group="Elektronik"), _Session(item))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_update_allows_product_group_on_generic():
    item = _fake_item(item_type="GENERIC", product_group="Diverses")
    response = await update_inventory_item(1, InventoryUpdate(product_group="Elektronik"), _Session(item))
    assert response.product_group == "Elektronik"
