"""Dachbodenfunde haben keinen Kaufpreis (Spec PR 1, Grill-Entscheid Q4):
ehrlich fehlend schlaegt falsch berechnet — nirgends 0 als Ersatzwert."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.api.routes.inventory import _recalculate_unrealized_metrics, _to_response


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
