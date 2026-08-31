from datetime import date

import pytest
from pydantic import ValidationError

from app.api.routes.inventory import InventoryAdd, InventoryUpdate


def _payload(**overrides):
    base = dict(set_name="Testartikel", buy_date=date(2026, 8, 30))
    base.update(overrides)
    return base


def test_lego_requires_set_number():
    with pytest.raises(ValidationError, match="Set-Nummer"):
        InventoryAdd(**_payload(item_type="LEGO", buy_price=10.0))


def test_lego_forces_group_and_derives_search_query():
    item = InventoryAdd(**_payload(item_type="LEGO", set_number="75331", buy_price=10.0, product_group="Elektronik"))
    assert item.product_group == "Lego"
    assert item.search_query == "LEGO 75331"


def test_lego_keeps_explicit_search_query():
    item = InventoryAdd(
        **_payload(item_type="LEGO", set_number="75331", buy_price=10.0, search_query="LEGO Razor Crest 75331")
    )
    assert item.search_query == "LEGO Razor Crest 75331"


def test_generic_needs_no_set_number_and_no_buy_price():
    item = InventoryAdd(**_payload(item_type="GENERIC"))
    assert item.set_number is None
    assert item.buy_price is None
    assert item.product_group == "Diverses"


def test_generic_ignores_set_number():
    item = InventoryAdd(**_payload(item_type="GENERIC", set_number="75331"))
    assert item.set_number is None


def test_unknown_item_type_is_rejected():
    with pytest.raises(ValidationError):
        InventoryAdd(**_payload(item_type="OBST"))


def test_update_rejects_explicit_null_product_group():
    with pytest.raises(ValidationError, match="product_group"):
        InventoryUpdate(product_group=None)


def test_update_without_product_group_is_fine():
    InventoryUpdate(set_name="X")
    assert "product_group" not in InventoryUpdate(set_name="X").model_fields_set
