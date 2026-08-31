"""Posten teilen kopiert Foto-DATEIEN — sonst zeigt der neue Artikel ins Leere,
sobald der alte geloescht wird (Grill-Entscheid Q17)."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes.inventory import SplitRequest, copy_item_photos, prorate_purchase, split_inventory_item


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


class _SplitSession:
    def __init__(self, item):
        self._item = item
        self.added = []
        self.rolled_back = False

    async def execute(self, _query):
        item = self._item

        class _Result:
            def scalar_one_or_none(self):
                return item

        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 2
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 8, 30, tzinfo=UTC)


def _source_item(**overrides):
    base = dict(
        id=1,
        item_type="GENERIC",
        set_number=None,
        set_name="Posten",
        product_group="Diverses",
        search_query=None,
        theme=None,
        image_url=None,
        buy_price=90.0,
        buy_shipping=6.0,
        buy_date=date(2026, 8, 1),
        buy_platform=None,
        buy_url=None,
        reference_url=None,
        condition="USED_COMPLETE",
        quantity=3,
        notes=None,
        status="HOLDING",
        current_market_price=None,
        market_price_updated_at=None,
        unrealized_profit=None,
        unrealized_roi_percent=None,
        sell_signal_active=False,
        sell_signal_reason=None,
        sell_price=None,
        sell_date=None,
        sell_platform=None,
        realized_profit=None,
        realized_roi_percent=None,
        photos=[],
        listings=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_split_rejects_sold_item():
    session = _SplitSession(_source_item(status="SOLD"))

    with pytest.raises(HTTPException) as exc:
        await split_inventory_item(1, SplitRequest(split_quantity=1), session)

    assert exc.value.status_code == 400


async def test_split_rejects_bad_quantity():
    for bad in (0, 3):
        session = _SplitSession(_source_item())

        with pytest.raises(HTTPException) as exc:
            await split_inventory_item(1, SplitRequest(split_quantity=bad), session)

        assert exc.value.status_code == 400


async def test_split_prorates_and_reduces():
    item = _source_item()
    session = _SplitSession(item)

    response = await split_inventory_item(1, SplitRequest(split_quantity=1), session)

    assert item.quantity == 2
    assert item.buy_price == 60.0
    assert item.buy_shipping == 4.0
    assert response.quantity == 1
    assert response.buy_price == 30.0
    assert response.buy_shipping == 2.0
    assert response.listings == []
