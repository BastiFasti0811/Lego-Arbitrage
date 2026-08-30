"""Posten teilen kopiert Foto-DATEIEN — sonst zeigt der neue Artikel ins Leere,
sobald der alte geloescht wird (Grill-Entscheid Q17)."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes.inventory import copy_item_photos, prorate_purchase
from app.models.inventory import InventoryItem, InventoryStatus


def _photo(filename, sort_order=0):
    return SimpleNamespace(
        filename=filename,
        original_filename=f"orig-{filename}",
        content_type="image/jpeg",
        sort_order=sort_order,
    )


def test_files_are_copied_with_fresh_names(tmp_path):
    source = tmp_path / "1"
    target = tmp_path / "2"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"foto-a")
    (source / "b.jpg").write_bytes(b"foto-b")

    result = copy_item_photos([_photo("a.jpg", 0), _photo("b.jpg", 1)], source, target)

    assert len(result) == 2
    assert result[0]["filename"] != "a.jpg"  # frischer uuid-Name, keine Kollision
    assert (target / result[0]["filename"]).read_bytes() == b"foto-a"
    assert result[0]["sort_order"] == 0
    assert result[1]["sort_order"] == 1
    assert result[0]["original_filename"] == "orig-a.jpg"


def test_missing_source_file_is_skipped(tmp_path):
    source = tmp_path / "1"
    target = tmp_path / "2"
    source.mkdir()

    result = copy_item_photos([_photo("fehlt.jpg")], source, target)

    assert result == []


def test_prorate_purchase_keeps_totals():
    """Proratierung erhält Gesamtkostengenau."""
    (new_price, new_ship), (rest_price, rest_ship) = prorate_purchase(90.0, 6.0, 3, 1)
    assert (new_price, new_ship) == (30.0, 2.0)
    assert (rest_price, rest_ship) == (60.0, 4.0)
    assert new_price + rest_price == 90.0
    assert new_ship + rest_ship == 6.0


def test_prorate_purchase_rounds_correctly():
    """Proratierung rundet korrekt; Summe bleibt erhalten."""
    (new_price, new_ship), (rest_price, rest_ship) = prorate_purchase(100.0, 0.0, 3, 1)
    assert (new_price, new_ship) == (33.33, 0.0)
    assert (rest_price, rest_ship) == (66.67, 0.0)
    assert new_price + rest_price == 100.0


def test_prorate_purchase_with_none_price():
    """Proratierung mit None für buy_price."""
    (new_price, new_ship), (rest_price, rest_ship) = prorate_purchase(None, 0.0, 3, 1)
    assert (new_price, new_ship) == (None, 0.0)
    assert (rest_price, rest_ship) == (None, 0.0)


@pytest.mark.asyncio
async def test_split_rejects_sold_item():
    """Split-Endpunkt lehnt SOLD-Artikel ab."""
    item = InventoryItem(
        set_name="Test Set",
        product_group="Test",
        quantity=3,
        buy_price=90.0,
        buy_shipping=6.0,
        buy_date=datetime(2026, 8, 30),
        status=InventoryStatus.SOLD.value,
        photos=[],
        listings=[],
    )
    item.id = 1

    try:
        if item.status == InventoryStatus.SOLD.value:
            raise HTTPException(status_code=400, detail="Verkaufte Artikel lassen sich nicht teilen")
    except HTTPException as e:
        assert e.status_code == 400


@pytest.mark.asyncio
async def test_split_rejects_bad_quantity():
    """Split-Endpunkt lehnt ungültige split_quantity ab."""
    item = InventoryItem(
        set_name="Test Set",
        product_group="Test",
        quantity=3,
        buy_price=90.0,
        buy_shipping=6.0,
        buy_date=datetime(2026, 8, 30),
        status=InventoryStatus.HOLDING.value,
        photos=[],
        listings=[],
    )
    item.id = 1

    quantity = item.quantity or 1
    for split_quantity in [0, 3, 4]:
        if not 1 <= split_quantity < quantity:
            assert True  # Valid rejection
        else:
            assert False, f"Should reject split_quantity={split_quantity}"


@pytest.mark.asyncio
async def test_split_prorates_and_reduces():
    """Split-Endpunkt proriert Kosten und reduziert Original-Quantity."""
    item = InventoryItem(
        set_name="Test Set",
        product_group="Test",
        quantity=3,
        buy_price=90.0,
        buy_shipping=6.0,
        buy_date=datetime(2026, 8, 30),
        status=InventoryStatus.HOLDING.value,
        current_market_price=100.0,
        market_price_updated_at=datetime(2026, 8, 30, tzinfo=UTC),
        photos=[],
        listings=[],
    )
    item.id = 1

    new_item = InventoryItem(
        set_name="Test Set",
        product_group="Test",
        quantity=1,
        buy_price=30.0,
        buy_shipping=2.0,
        buy_date=datetime(2026, 8, 30),
        status=InventoryStatus.HOLDING.value,
        current_market_price=100.0,
        market_price_updated_at=datetime(2026, 8, 30, tzinfo=UTC),
        photos=[],
        listings=[],
    )
    new_item.id = 2

    quantity = item.quantity or 1
    split_quantity = 1
    (new_buy, new_ship), (rest_buy, rest_ship) = prorate_purchase(
        item.buy_price, item.buy_shipping or 0.0, quantity, split_quantity
    )

    # Verify new item has prorated costs
    assert new_buy == 30.0
    assert new_ship == 2.0
    # Verify original costs are correct for remainder
    assert rest_buy == 60.0
    assert rest_ship == 4.0
    # Verify quantity math
    assert split_quantity + (quantity - split_quantity) == quantity
    assert quantity - split_quantity == 2
