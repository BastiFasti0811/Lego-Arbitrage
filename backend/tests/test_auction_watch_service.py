from app.services.auction_watch import (
    build_fee_profile,
    calculate_auction_purchase_total,
    detect_source_platform,
    solve_max_bid,
)


def test_detect_source_platform_handles_supported_sources():
    assert detect_source_platform("https://www.catawiki.com/de/l/102824557", None) == "CATAWIKI"
    assert detect_source_platform("https://www.whatnot.com/de-DE/listing/123", None) == "WHATNOT"
    assert detect_source_platform("https://www.bricklink.com/v2/catalog/catalogitem.page?S=75313-1", None) == "BRICKLINK"


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
