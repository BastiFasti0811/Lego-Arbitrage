"""Schema-Grenzen spiegeln die VARCHAR-Laengen des Modells.

Postgres lehnt zu lange Werte mit StringDataRightTruncation ab, was ohne
Schema-Grenze als 500 beim Client ankommt. SQLite in den Tests speichert
still, deshalb pruefen diese Tests die Schemas direkt gegen das Modell.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from app.api.routes.inventory import InventoryAdd, InventoryUpdate, SellRequest
from app.models.inventory import InventoryItem

LIMITED_FIELDS = ["set_name", "product_group", "search_query", "theme", "buy_platform", "condition", "storage_location"]


def _column_length(field: str) -> int:
    return InventoryItem.__table__.c[field].type.length


def _add(**overrides) -> InventoryAdd:
    data = {"set_number": "42055", "set_name": "Bagger", "buy_date": date(2024, 1, 1)}
    data.update(overrides)
    return InventoryAdd(**data)


@pytest.mark.parametrize("field", LIMITED_FIELDS)
def test_update_accepts_column_length_and_rejects_one_more(field):
    n = _column_length(field)
    InventoryUpdate(**{field: "x" * n})
    with pytest.raises(ValidationError):
        InventoryUpdate(**{field: "x" * (n + 1)})


@pytest.mark.parametrize("field", ["set_number", *LIMITED_FIELDS])
def test_add_rejects_value_longer_than_column(field):
    n = _column_length(field)
    # product_group ersetzt der Validator bei Lego; nur ein Generic-Posten behaelt ihn.
    overrides = {"item_type": "GENERIC"} if field == "product_group" else {}
    with pytest.raises(ValidationError):
        _add(**overrides, **{field: "x" * (n + 1)})


def test_add_ignores_length_of_values_the_validator_replaces():
    # Lego: Warengruppe wird "Lego"; Generic: Setnummer wird verworfen.
    assert _add(product_group="x" * 150).product_group == "Lego"
    assert _add(item_type="GENERIC", set_number="x" * 30).set_number is None


def test_sell_platform_limited_like_its_column():
    n = InventoryItem.__table__.c["sell_platform"].type.length
    SellRequest(sell_price=10, sell_platform="x" * n)
    with pytest.raises(ValidationError):
        SellRequest(sell_price=10, sell_platform="x" * (n + 1))


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_blank_storage_location_becomes_null(raw):
    assert _add(storage_location=raw).storage_location is None
    update = InventoryUpdate(storage_location=raw)
    assert update.storage_location is None
    # Leeren muss als gesetztes Feld beim PATCH ankommen, sonst bliebe der alte Wert.
    assert update.model_dump(exclude_unset=True) == {"storage_location": None}


def test_storage_location_is_trimmed():
    assert InventoryUpdate(storage_location="  Dachboden Kiste 3 ").storage_location == "Dachboden Kiste 3"
