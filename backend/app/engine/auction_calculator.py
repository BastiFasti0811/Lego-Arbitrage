"""Auction bid guardrails for fee-heavy marketplaces like Catawiki."""

from dataclasses import dataclass

from app.engine.roi_calculator import calculate_ebay_fees, estimate_shipping


@dataclass
class AuctionFeeProfile:
    """Fee structure applied by the buy-side marketplace."""

    platform: str
    buyer_fee_rate: float
    buyer_fee_fixed: float
    fee_applies_to_shipping: bool = False


@dataclass
class AuctionBidResult:
    """Maximum hammer-price recommendation for an auction."""

    platform: str
    target_roi_percent: float
    expected_sale_price: float
    net_sale_revenue: float
    purchase_shipping: float
    buyer_fee_rate: float
    buyer_fee_fixed: float
    fee_applies_to_shipping: bool
    packaging_cost: float
    sale_shipping: float
    total_selling_costs: float
    max_bid: float
    buyer_fee_at_max_bid: float
    total_purchase_cost_at_max_bid: float
    expected_profit_at_max_bid: float
    expected_roi_at_max_bid: float
    break_even_bid: float
    break_even_total_purchase_cost: float
    break_even_buyer_fee: float


def _purchase_total_from_bid(
    bid: float,
    *,
    purchase_shipping: float,
    profile: AuctionFeeProfile,
) -> tuple[float, float]:
    """Return total buy-side cost and marketplace fee for a hammer price."""
    fee_base = bid + purchase_shipping if profile.fee_applies_to_shipping else bid
    buyer_fee = fee_base * profile.buyer_fee_rate + profile.buyer_fee_fixed
    total_purchase_cost = bid + purchase_shipping + buyer_fee
    return round(total_purchase_cost, 2), round(buyer_fee, 2)


def calculate_auction_purchase_total(
    *,
    bid: float,
    purchase_shipping: float,
    profile: AuctionFeeProfile,
) -> tuple[float, float]:
    """Public helper that evaluates a concrete hammer price."""
    return _purchase_total_from_bid(
        bid,
        purchase_shipping=purchase_shipping,
        profile=profile,
    )


def _solve_max_bid(
    target_total_purchase_cost: float,
    *,
    purchase_shipping: float,
    profile: AuctionFeeProfile,
) -> float:
    """Solve the hammer-price ceiling for the given purchase-cost budget."""
    if target_total_purchase_cost <= 0:
        return 0.0

    if profile.fee_applies_to_shipping:
        numerator = (target_total_purchase_cost - profile.buyer_fee_fixed) / (1 + profile.buyer_fee_rate)
        max_bid = numerator - purchase_shipping
    else:
        numerator = target_total_purchase_cost - purchase_shipping - profile.buyer_fee_fixed
        max_bid = numerator / (1 + profile.buyer_fee_rate)

    return round(max(0.0, max_bid), 2)


def calculate_max_auction_bid(
    *,
    expected_sale_price: float,
    target_roi_percent: float,
    profile: AuctionFeeProfile,
    purchase_shipping: float | None = None,
    sale_shipping: float | None = None,
    packaging_cost: float | None = None,
    uvp: float | None = None,
) -> AuctionBidResult:
    """Calculate the max hammer price that still clears the target ROI."""
    shipping = estimate_shipping(uvp=uvp or expected_sale_price)
    if purchase_shipping is None:
        purchase_shipping = shipping.purchase_shipping
    if sale_shipping is None:
        sale_shipping = shipping.sale_shipping
    if packaging_cost is None:
        packaging_cost = shipping.packaging_cost

    ebay_provision, payment_fee = calculate_ebay_fees(expected_sale_price)
    total_selling_costs = round(ebay_provision + payment_fee + packaging_cost + sale_shipping, 2)
    net_sale_revenue = round(expected_sale_price - total_selling_costs, 2)

    target_total_purchase_cost = net_sale_revenue / (1 + target_roi_percent / 100) if net_sale_revenue > 0 else 0.0
    max_bid = _solve_max_bid(
        target_total_purchase_cost,
        purchase_shipping=purchase_shipping,
        profile=profile,
    )
    total_purchase_cost_at_max_bid, buyer_fee_at_max_bid = _purchase_total_from_bid(
        max_bid,
        purchase_shipping=purchase_shipping,
        profile=profile,
    )
    expected_profit_at_max_bid = round(net_sale_revenue - total_purchase_cost_at_max_bid, 2)
    expected_roi_at_max_bid = (
        round(expected_profit_at_max_bid / total_purchase_cost_at_max_bid * 100, 1)
        if total_purchase_cost_at_max_bid > 0
        else 0.0
    )

    break_even_total_purchase_cost = max(0.0, net_sale_revenue)
    break_even_bid = _solve_max_bid(
        break_even_total_purchase_cost,
        purchase_shipping=purchase_shipping,
        profile=profile,
    )
    break_even_purchase_cost, break_even_buyer_fee = _purchase_total_from_bid(
        break_even_bid,
        purchase_shipping=purchase_shipping,
        profile=profile,
    )

    return AuctionBidResult(
        platform=profile.platform,
        target_roi_percent=round(target_roi_percent, 1),
        expected_sale_price=round(expected_sale_price, 2),
        net_sale_revenue=net_sale_revenue,
        purchase_shipping=round(purchase_shipping, 2),
        buyer_fee_rate=profile.buyer_fee_rate,
        buyer_fee_fixed=round(profile.buyer_fee_fixed, 2),
        fee_applies_to_shipping=profile.fee_applies_to_shipping,
        packaging_cost=round(packaging_cost, 2),
        sale_shipping=round(sale_shipping, 2),
        total_selling_costs=total_selling_costs,
        max_bid=max_bid,
        buyer_fee_at_max_bid=buyer_fee_at_max_bid,
        total_purchase_cost_at_max_bid=total_purchase_cost_at_max_bid,
        expected_profit_at_max_bid=expected_profit_at_max_bid,
        expected_roi_at_max_bid=expected_roi_at_max_bid,
        break_even_bid=break_even_bid,
        break_even_total_purchase_cost=break_even_purchase_cost,
        break_even_buyer_fee=break_even_buyer_fee,
    )
