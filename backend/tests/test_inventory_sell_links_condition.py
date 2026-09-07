"""Die Anzeige darf nur behaupten, was in der Zeile steht.

`get_sell_links` schrieb "Zustand: Neu & Originalverpackt (OVP)" fest verdrahtet
in den Kleinanzeigen-Text und haengte "NEU OVP" an jeden eBay-Titel, ohne
`item.condition` je zu lesen. Ein Klick kopiert diesen Text und oeffnet das
Anzeigenformular — ein gebrauchtes Set wurde damit als neu und originalverpackt
inseriert.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.api.routes.inventory import get_sell_links


def _item(**overrides):
    base = dict(
        id=1,
        set_number="10497",
        set_name="Galaxy Explorer",
        theme="Icons",
        image_url=None,
        item_type="LEGO",
        product_group="Lego",
        search_query=None,
        buy_price=80.0,
        buy_shipping=0.0,
        buy_date=date(2026, 8, 1),
        buy_platform=None,
        buy_url=None,
        reference_url=None,
        condition="NEW_SEALED",
        quantity=1,
        notes=None,
        photos=[],
        listings=[],
        current_market_price=120.0,
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


class _GetItemSession:
    """Fake session fuer get_sell_links: session.get() liefert das Item direkt."""

    def __init__(self, item):
        self._item = item

    async def get(self, _model, _item_id):
        return self._item


async def _links(**overrides):
    item = _item(**overrides)
    return await get_sell_links(item_id=1, session=_GetItemSession(item))


@pytest.mark.asyncio
async def test_used_item_is_never_advertised_as_new():
    response = await _links(condition="USED_COMPLETE")

    assert "Zustand: Gebraucht, komplett" in response.kleinanzeigen_text
    assert "OVP" not in response.kleinanzeigen_text
    assert "OVP" not in response.ebay_title
    assert "NEU" not in response.ebay_title


@pytest.mark.asyncio
async def test_incomplete_item_says_so():
    response = await _links(condition="USED_INCOMPLETE")

    assert "Zustand: Gebraucht, unvollständig" in response.kleinanzeigen_text
    assert "unvollständig" in response.ebay_title


@pytest.mark.asyncio
async def test_sealed_item_keeps_the_ovp_wording():
    response = await _links(condition="NEW_SEALED")

    assert "Zustand: Neu & Originalverpackt (OVP)" in response.kleinanzeigen_text
    assert response.ebay_title.endswith("NEU OVP")


@pytest.mark.asyncio
async def test_open_box_is_not_sold_as_sealed():
    response = await _links(condition="NEW_OPEN_BOX")

    assert "Zustand: Neu, geöffnet" in response.kleinanzeigen_text
    assert "OVP" not in response.kleinanzeigen_text
    assert "OVP" not in response.ebay_title


@pytest.mark.asyncio
async def test_unknown_condition_claims_nothing():
    """Kein Text ist richtig: der Verkaeufer traegt selbst ein, was er geprueft hat."""
    response = await _links(condition="UNKNOWN")

    assert "Zustand" not in response.kleinanzeigen_text
    assert "OVP" not in response.ebay_title
    assert "NEU" not in response.ebay_title
    assert response.ebay_title == "LEGO 10497 Galaxy Explorer"


@pytest.mark.asyncio
async def test_unmapped_condition_falls_back_to_claiming_nothing():
    """Ein Token, das keiner kennt, darf keine Zusage erzeugen.

    Solange die Pydantic-Schemata `condition` nicht gegen das Enum pruefen, kann
    jeder Client einen beliebigen String speichern. normalize_condition macht
    daraus UNKNOWN — und UNKNOWN behauptet nichts.
    """
    response = await _links(condition="Neu & Geoeffnet")

    assert "Zustand" not in response.kleinanzeigen_text
    assert "OVP" not in response.ebay_title


@pytest.mark.asyncio
async def test_generic_item_without_set_number_still_carries_the_condition():
    response = await _links(
        set_number=None,
        set_name="Bohrmaschine",
        item_type="GENERIC",
        product_group="Elektronik",
        condition="USED_COMPLETE",
    )

    assert response.ebay_title == "Bohrmaschine gebraucht"
    assert "Zustand: Gebraucht, komplett" in response.kleinanzeigen_text
