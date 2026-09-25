import pytest

from app.scrapers.base import ScrapedPrice
from app.services import auction_watch
from app.services.auction_watch import (
    build_fee_profile,
    calculate_auction_purchase_total,
    detect_source_platform,
    solve_max_bid,
)


def test_detect_source_platform_handles_supported_sources():
    assert detect_source_platform("https://www.catawiki.com/de/l/102824557", None) == "CATAWIKI"
    assert detect_source_platform("https://www.whatnot.com/de-DE/listing/123", None) == "WHATNOT"
    assert (
        detect_source_platform("https://www.bricklink.com/v2/catalog/catalogitem.page?S=75313-1", None)
        == "BRICKLINK"
    )


def test_solve_max_bid_round_trip_for_catawiki_defaults():
    fee_profile = build_fee_profile(source_platform="CATAWIKI")

    max_bid = solve_max_bid(
        target_total_purchase_cost=121.0,
        purchase_shipping=13.0,
        fee_profile=fee_profile,
    )
    total_purchase_cost, buyer_fee = calculate_auction_purchase_total(
        bid=max_bid,
        purchase_shipping=13.0,
        fee_profile=fee_profile,
    )

    assert max_bid == 96.33
    assert buyer_fee == 11.67
    assert total_purchase_cost == 121.0


def test_solve_max_bid_when_fee_also_applies_to_shipping():
    fee_profile = build_fee_profile(
        source_platform="AUCTION",
        buyer_fee_rate=0.10,
        buyer_fee_fixed=2.0,
        fee_applies_to_shipping=True,
    )

    max_bid = solve_max_bid(
        target_total_purchase_cost=100.0,
        purchase_shipping=10.0,
        fee_profile=fee_profile,
    )
    total_purchase_cost, buyer_fee = calculate_auction_purchase_total(
        bid=max_bid,
        purchase_shipping=10.0,
        fee_profile=fee_profile,
    )

    assert max_bid == 79.09
    assert buyer_fee == 10.91
    assert total_purchase_cost == 100.0


@pytest.mark.parametrize(("condition", "shipping", "sources", "allowed"), [
    ("NEW_SEALED", 13, ["EBAY_SOLD", "BRICKMERGE"], True),
    ("UNKNOWN", 13, ["EBAY_SOLD", "BRICKMERGE"], False),
    ("NEW_SEALED", None, ["EBAY_SOLD", "BRICKMERGE"], False),
    ("NEW_SEALED", 13, ["EBAY_ACTIVE"], False),
    ("NEW_SEALED", 13, [], False),
    # Nur BrickMerge: Limit wird berechnet (gelb), aber nie automatisch freigegeben.
    ("NEW_SEALED", 13, ["BRICKMERGE"], False),
    # Einzelquelle ohne BrickMerge bleibt gesperrt.
    ("NEW_SEALED", 13, ["BRICKECONOMY"], False),
])
async def test_bid_permission_requires_market_condition_and_shipping(
    monkeypatch, condition, shipping, sources, allowed,
):
    async def context(**kwargs):
        return [ScrapedPrice(source=s, price_eur=200) for s in sources], "LEGO", "Star Wars", 2020, 400, "AVAILABLE"
    monkeypatch.setattr(auction_watch, "gather_market_context", context)
    result = await auction_watch.evaluate_auction(
        set_number="75313", current_bid=10, purchase_shipping=shipping, condition=condition,
    )
    assert result.can_bid_now is allowed
    # Retail UVP (400) must never inflate the resale calculation (market 200).
    assert result.bid_result.expected_sale_price <= 200
    if not allowed:
        assert result.bid_status == "NEEDS_REVIEW"


async def test_condition_reduces_auction_ceiling_not_only_risk(monkeypatch):
    async def context(**kwargs):
        prices = [ScrapedPrice(source=s, price_eur=200) for s in ("EBAY_SOLD", "BRICKMERGE")]
        return prices, "LEGO", "City", 2020, None, "RETIRED"
    monkeypatch.setattr(auction_watch, "gather_market_context", context)
    sealed = await auction_watch.evaluate_auction(
        set_number="10282", current_bid=10, purchase_shipping=13, condition="NEW_SEALED",
    )
    used = await auction_watch.evaluate_auction(
        set_number="10282", current_bid=10, purchase_shipping=13, condition="USED_COMPLETE",
    )
    assert used.bid_result.expected_sale_price == sealed.bid_result.expected_sale_price * 0.7
    assert used.bid_result.max_bid < sealed.bid_result.max_bid


async def test_brickmerge_fallback_is_marked_and_used_for_the_ceiling(monkeypatch):
    async def context(**kwargs):
        # Zwei Quellen >30 % auseinander: kein Konsens, BrickMerge als Rueckfall.
        prices = [ScrapedPrice(source="EBAY_SOLD", price_eur=300), ScrapedPrice(source="BRICKMERGE", price_eur=180)]
        return prices, "LEGO", "Star Wars", 2019, None, "RETIRED"
    monkeypatch.setattr(auction_watch, "gather_market_context", context)
    result = await auction_watch.evaluate_auction(
        set_number="75313", current_bid=10, purchase_shipping=13, condition="NEW_SEALED",
    )
    assert (result.price_basis, result.market_price_used) == ("BRICKMERGE_ONLY", 180)
    assert result.bid_result.expected_sale_price == 180
    assert result.warnings[0].startswith("Nur BrickMerge-Bestpreis")
    # Gerechnet ja, freigegeben nein: kein "Jetzt bieten" ohne Gegenprobe.
    assert not result.can_bid_now and result.bid_status == "NEEDS_REVIEW"


async def test_brickmerge_above_resale_is_never_the_fallback(monkeypatch):
    # Ausgelaufenes Set: Haendlerpreis ueber den eBay-Verkaeufen (Review #32, Blocker 2).
    async def context(**kwargs):
        prices = [ScrapedPrice(source="EBAY_SOLD", price_eur=350), ScrapedPrice(source="BRICKMERGE", price_eur=540)]
        return prices, "LEGO", "Harry Potter", 2023, 430, "RETIRED"
    monkeypatch.setattr(auction_watch, "gather_market_context", context)
    result = await auction_watch.evaluate_auction(
        set_number="76417", current_bid=10, purchase_shipping=13, condition="NEW_SEALED",
    )
    assert result.price_basis is None
    assert result.bid_status == "NEEDS_REVIEW" and not result.can_bid_now
