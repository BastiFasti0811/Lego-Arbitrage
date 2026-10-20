"""Die Anzeige darf nur behaupten, was in der Zeile steht.

Der fruehere `/sell-links`-Text schrieb "Zustand: Neu & Originalverpackt (OVP)"
fest verdrahtet und haengte "NEU OVP" an jeden Titel, ohne `item.condition` je
zu lesen — ein gebrauchtes Set wurde damit als neu und originalverpackt
inseriert. Die Vorlage lebt heute als Rueckfall von "Inserat vorbereiten"
(`template_listing_text`), wenn die KI ausfaellt; dieselben Regeln gelten.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.services.listing_prepare import template_listing_text


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


async def _links(**overrides):
    """Titel und Text der Vorlage in den Formen, die die alten Asserts lesen."""
    item = _item(**overrides)
    text = template_listing_text(item, price=120.0, price_type="VB")
    return SimpleNamespace(kleinanzeigen_text=f"{text.title}\n\n{text.body}", ebay_title=text.title)


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


def test_template_carries_price_with_vb_and_quantity():
    item = _item(quantity=3)
    text = template_listing_text(item, price=119.6, price_type="VB")

    assert "Preis: 120€ VB" in text.body
    assert "3 Stück vorhanden" in text.body
    assert text.platform_category == ""


def test_template_fixed_price_has_no_vb():
    text = template_listing_text(_item(), price=50.0, price_type="FIXED")

    assert "Preis: 50€\n" in text.body
    assert "VB" not in text.body
