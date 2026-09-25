"""Review-Findings aus PR #31: Deal-Checker-Fallback und unbekannter Versand."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes import analysis, auctions
from app.models.auction_watch import AuctionWatchItem

URL = "https://www.catawiki.com/de/l/102824557-lego-10282-adidas-originals-superstar"


@pytest.mark.asyncio
async def test_unreadable_catawiki_page_falls_back_to_url_instead_of_500(monkeypatch):
    # Consent- oder Challenge-Seite mit Status 200 und ohne h1.
    monkeypatch.setattr(analysis, "get_settings_map", AsyncMock(return_value={}))
    monkeypatch.setattr(
        analysis.CatawikiScraper, "_fetch", AsyncMock(return_value="<html><body><p>Bitte bestaetigen</p></body></html>")
    )

    result = await analysis.parse_listing_url(analysis.ParseUrlRequest(url=URL))

    assert result.platform == "CATAWIKI"
    assert result.set_number == "10282"


def test_watch_shipping_column_allows_unknown():
    assert AuctionWatchItem.__table__.c["purchase_shipping"].nullable


@pytest.mark.asyncio
async def test_add_watch_keeps_unknown_shipping_as_none(monkeypatch):
    # Frueher `or 0.0`: ein unbekannter Versand sah wie kostenloser aus und die
    # Sperre "Versandkosten fehlen" in evaluate_auction griff nie.
    captured = {}

    async def evaluate(item, lego_set):
        captured["shipping"] = item.purchase_shipping

    monkeypatch.setattr(auctions, "validate_marketplace_url", lambda *args: None)
    monkeypatch.setattr(auctions, "_apply_watch_evaluation", evaluate)
    monkeypatch.setattr(auctions, "_serialize_watch", lambda item, lego_set: item)

    lego_set = SimpleNamespace(id=1, set_number="10282")
    no_existing = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))
    set_row = SimpleNamespace(scalar_one_or_none=lambda: lego_set)
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[no_existing, set_row]),
        add=lambda obj: None, commit=AsyncMock(), refresh=AsyncMock(), flush=AsyncMock(),
    )
    data = auctions.AuctionWatchCreate(set_number="10282", source_url=URL, current_bid=50)

    item = await auctions.add_auction_watch(data, session=session)

    assert captured["shipping"] is None
    assert item.purchase_shipping is None
