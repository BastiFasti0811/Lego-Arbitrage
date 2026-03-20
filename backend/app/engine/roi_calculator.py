"""ROI Calculator — full cost structure for German LEGO market.

Implements the complete cost calculation from the agent spec:
- Purchase costs (price + shipping + travel)
- Selling costs (eBay provision, payment fees, packaging, insured shipping)
- Net profit and ROI calculation
- Annualized ROI based on expected holding time
"""

from dataclasses import dataclass

from app.config import settings


@dataclass
class ShippingEstimate:
    """Estimated shipping costs based on set size."""

    category: str  # Klein, Mittel, Groß, Sehr groß
    purchase_shipping: float
    sale_shipping: float
    packaging_cost: float


@dataclass
class ROIResult:
    """Complete ROI calculation result with full cost breakdown."""

    # ── Input ────────────────────────────────────────────
    purchase_price: float
    market_price: float  # Expected selling price

    # ── Purchase Costs ───────────────────────────────────
    purchase_shipping: float
    total_purchase_cost: float  # purchase_price + shipping

    # ── Selling Costs ────────────────────────────────────
    ebay_provision: float  # 12.9% + 0.35€
    payment_fee: float  # 1.9% + 0.35€
    packaging_cost: float
    sale_shipping: float  # Insured shipping
    total_selling_costs: float

    # ── Results ──────────────────────────────────────────
    net_revenue: float  # market_price - selling costs
    net_profit: float  # net_revenue - total_purchase_cost
    roi_percent: float  # (profit / purchase_cost) * 100
    annualized_roi: float  # roi / (months / 12)
    holding_months: float

    # ── Breakeven ────────────────────────────────────────
    breakeven_price: float  # Min selling price to break even

    @property
    def is_profitable(self) -> bool:
        return self.net_profit > 0


def estimate_shipping(box_dimensions: str | None = None, weight_kg: float | None = None,
                      uvp: float | None = None) -> ShippingEstimate:
    """Estimate shipping costs based on set size/value.

    Uses UVP as a proxy for set size if no dimensions available.
    """
    # Heuristic: higher UVP → bigger box
    if uvp:
        if uvp < 30:
            cat = "Klein"
            return ShippingEstimate(cat, settings.shipping_small, settings.shipping_small + 2, 5.0)
        elif uvp < 80:
            cat = "Mittel"
            return ShippingEstimate(cat, settings.shipping_medium, settings.shipping_medium + 3, 8.0)
        elif uvp < 200:
            cat = "Groß"
            return ShippingEstimate(cat, settings.shipping_large, settings.shipping_large + 5, 12.0)
        else:
            cat = "Sehr groß"
            return ShippingEstimate(cat, settings.shipping_xlarge, settings.shipping_xlarge + 8, 18.0)

    # Default: medium
    return ShippingEstimate("Mittel", settings.shipping_medium, settings.shipping_medium + 3, 8.0)


def calculate_ebay_fees(selling_price: float) -> tuple[float, float]:
    """Calculate eBay selling fees.

    Returns: (ebay_provision, payment_fee)
    """
    provision = selling_price * settings.ebay_provision_rate + settings.ebay_provision_fixed
    payment = selling_price * settings.ebay_payment_rate + settings.ebay_payment_fixed
    return round(provision, 2), round(payment, 2)


def calculate_roi(
    purchase_price: float,
    market_price: float,
    purchase_shipping: float | None = None,
    sale_shipping: float | None = None,
    packaging_cost: float | None = None,
    holding_months: float = 12.0,
    uvp: float | None = None,
) -> ROIResult:
    """Calculate full ROI with all costs.

    Args:
        purchase_price: What we pay for the set
        market_price: Expected selling price (market consensus)
        purchase_shipping: Cost to receive the set (None = estimate)
        sale_shipping: Cost to ship to buyer (None = estimate)
        packaging_cost: Packaging material cost (None = estimate)
        holding_months: Expected time to hold before selling
        uvp: Original retail price (for shipping estimation)
    """
    # Estimate shipping if not provided
    shipping = estimate_shipping(uvp=uvp or purchase_price)
    if purchase_shipping is None:
        purchase_shipping = shipping.purchase_shipping
    if sale_shipping is None:
        sale_shipping = shipping.sale_shipping
    if packaging_cost is None:
        packaging_cost = shipping.packaging_cost

    # Purchase total
    total_purchase = purchase_price + purchase_shipping

    # eBay fees on market price
    ebay_provision, payment_fee = calculate_ebay_fees(market_price)

    # Total selling costs
    total_selling = ebay_provision + payment_fee + packaging_cost + sale_shipping

    # Net revenue & profit
    net_revenue = market_price - total_selling
    net_profit = net_revenue - total_purchase

    # ROI
    roi = (net_profit / total_purchase * 100) if total_purchase > 0 else 0
    annual_roi = (roi / (holding_months / 12)) if holding_months > 0 else roi

    # Breakeven: what selling price makes profit = 0
    # breakeven - fees(breakeven) - packaging - shipping = total_purchase
    # breakeven * (1 - 0.129 - 0.019) - 0.35 - 0.35 - packaging - sale_shipping = total_purchase
    fee_rate = settings.ebay_provision_rate + settings.ebay_payment_rate
    fixed_costs = settings.ebay_provision_fixed + settings.ebay_payment_fixed + packaging_cost + sale_shipping
    breakeven = (total_purchase + fixed_costs) / (1 - fee_rate)

    return ROIResult(
        purchase_price=purchase_price,
        market_price=market_price,
        purchase_shipping=round(purchase_shipping, 2),
        total_purchase_cost=round(total_purchase, 2),
        ebay_provision=round(ebay_provision, 2),
        payment_fee=round(payment_fee, 2),
        packaging_cost=round(packaging_cost, 2),
        sale_shipping=round(sale_shipping, 2),
        total_selling_costs=round(total_selling, 2),
        net_revenue=round(net_revenue, 2),
        net_profit=round(net_profit, 2),
        roi_percent=round(roi, 1),
        annualized_roi=round(annual_roi, 1),
        holding_months=holding_months,
        breakeven_price=round(breakeven, 2),
    )
