"""Market Consensus Engine — aggregates prices from multiple sources.

Implements weighted consensus pricing:
- eBay Sold: 40% (actual transactions)
- BrickEconomy: 30% (global market data)
- Idealo: 20% (German retail)
- BrickMerge: 10% (shop aggregator)
"""

from dataclasses import dataclass, field

import structlog

from app.config import settings
from app.scrapers.base import ScrapedPrice

logger = structlog.get_logger()


SOURCE_WEIGHTS = {
    "EBAY_SOLD": settings.weight_ebay_sold,
    "BRICKECONOMY": settings.weight_brickeconomy,
    "IDEALO": settings.weight_idealo,
    "BRICKMERGE": settings.weight_brickmerge,
    "AMAZON": 0.0,  # Amazon not used for consensus (often inflated)
    "KLEINANZEIGEN": 0.0,  # Asking prices, not market value
    "LEGO_COM": 0.0,  # UVP, not market value
}


@dataclass
class MarketConsensus:
    """Result of multi-source price aggregation."""

    consensus_price: float
    num_sources: int
    source_prices: dict[str, float] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    price_range_low: float = 0.0
    price_range_high: float = 0.0
    divergence_percent: float = 0.0  # Max divergence between sources
    is_reliable: bool = True
    warnings: list[str] = field(default_factory=list)


def calculate_consensus(prices: list[ScrapedPrice]) -> MarketConsensus:
    """Calculate weighted consensus price from multiple sources.

    Logic:
    1. If all sources agree (±10%): Use median
    2. If large divergence (>20%): Use weighted average with warnings
    3. If only 1-2 sources: Conservative estimate with warning
    """
    # Filter to sources that have weight > 0
    market_prices = {}
    for p in prices:
        if p.source in SOURCE_WEIGHTS and p.is_reliable and p.price_eur > 0:
            # For eBay, prefer median_price if available
            price = p.median_price if p.median_price and p.source == "EBAY_SOLD" else p.price_eur
            market_prices[p.source] = price

    result = MarketConsensus(
        consensus_price=0.0,
        num_sources=len(market_prices),
        source_prices=market_prices,
    )

    if not market_prices:
        result.is_reliable = False
        result.warnings.append("Keine Marktpreisdaten verfügbar!")
        return result

    prices_list = list(market_prices.values())
    result.price_range_low = min(prices_list)
    result.price_range_high = max(prices_list)

    # Calculate divergence
    if len(prices_list) >= 2:
        mean = sum(prices_list) / len(prices_list)
        if mean > 0:
            result.divergence_percent = (result.price_range_high - result.price_range_low) / mean

    # ── Case 1: Only one source ──────────────────────────
    if len(market_prices) == 1:
        result.consensus_price = prices_list[0]
        result.is_reliable = False
        result.warnings.append("Nur 1 Datenquelle — unsichere Datenlage!")
        return result

    # ── Case 2: Sources agree (±10%) → Use median ───────
    median = sorted(prices_list)[len(prices_list) // 2]
    if result.divergence_percent <= 0.10:
        result.consensus_price = median
        return result

    # ── Case 3: Divergence → Weighted average ────────────
    if result.divergence_percent > settings.price_divergence_warning:
        result.warnings.append(
            f"Große Preisabweichung zwischen Quellen: {result.divergence_percent:.0%}. "
            f"Gewichteter Durchschnitt wird verwendet."
        )

    # Weighted average
    total_weight = 0.0
    weighted_sum = 0.0
    for source, price in market_prices.items():
        weight = SOURCE_WEIGHTS.get(source, 0.0)
        if weight > 0:
            weighted_sum += price * weight
            total_weight += weight
            result.weights_used[source] = weight

    if total_weight > 0:
        result.consensus_price = round(weighted_sum / total_weight, 2)
    else:
        # Fallback: simple median
        result.consensus_price = median

    # Reliability check
    if len(market_prices) < 2:
        result.is_reliable = False
    if result.divergence_percent > 0.30:
        result.is_reliable = False
        result.warnings.append("Extreme Preisabweichung >30% — manuell verifizieren!")

    return result
