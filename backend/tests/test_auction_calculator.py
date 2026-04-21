from app.engine.auction_calculator import (
    AuctionFeeProfile,
    calculate_auction_purchase_total,
    calculate_max_auction_bid,
)


def test_catawiki_max_bid_uses_fee_on_hammer_only():
    profile = AuctionFeeProfile(
        platform="CATAWIKI",
        buyer_fee_rate=0.09,
        buyer_fee_fixed=3.0,
        fee_applies_to_shipping=False,
    )

    result = calculate_max_auction_bid(
        expected_sale_price=200.0,
        target_roi_percent=15.0,
        profile=profile,
        purchase_shipping=13.0,
        sale_shipping=7.0,
        packaging_cost=4.0,
        uvp=200.0,
    )

    assert result.max_bid == 111.93
    assert result.buyer_fee_at_max_bid == 13.07
    assert result.total_purchase_cost_at_max_bid == 138.0
    assert result.expected_roi_at_max_bid == 15.0
    assert result.break_even_bid == 130.92


def test_purchase_total_can_include_shipping_in_fee_base():
    profile = AuctionFeeProfile(
        platform="TEST",
        buyer_fee_rate=0.1,
        buyer_fee_fixed=2.0,
        fee_applies_to_shipping=True,
    )

    total_purchase_cost, buyer_fee = calculate_auction_purchase_total(
        bid=50.0,
        purchase_shipping=10.0,
        profile=profile,
    )

    assert total_purchase_cost == 68.0
    assert buyer_fee == 8.0
