"""Market Consensus Engine — aggregates prices from multiple sources.

Implements weighted consensus pricing with outlier detection:
- eBay Sold: 40% (actual transactions — most reliable)
- BrickMerge: 30% (German shop aggregator with price history)
- BrickEconomy: 20% (global market data)
- Idealo: 10% (often wrong product match — lowest trust)
"""

from dataclasses import dataclass, field

import structlog

from app.config import settings
from app.scrapers.base import ScrapedPrice

logger = structlog.get_logger()


# Rebalanced weights: BrickMerge + eBay Sold are primary sources
SOURCE_WEIGHTS = {
    "EBAY_SOLD": 0.40,      # Actual transactions — gold standard
    "BRICKMERGE": 0.30,     # German shop aggregator — reliable
    "BRICKECONOMY": 0.20,   # Global market data — good reference
    "IDEALO": 0.10,         # Often wrong product — lowest trust
    "AMAZON": 0.0,          # Not used for consensus (often inflated)
    "KLEINANZEIGEN": 0.0,   # Asking prices, not market value
    "LEGO_COM": 0.0,        # UVP, not market value
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
    outliers_removed: dict[str, float] = field(default_factory=dict)


def _remove_outliers(
    market_prices: dict[str, float],
    warnings: list[str],
) -> tuple[dict[str, float], dict[str, float]]:
    """Remove obvious outlier prices.

    Strategy:
    1. If we have 3+ sources, remove any price that deviates >60% from the median
    2. If we have 2 sources and they differ >80%, flag as unreliable
    3. If a price is < 5€ for a LEGO set, it's almost certainly wrong
    """
    outliers = {}

    if not market_prices:
        return market_prices, outliers

    prices_list = list(market_prices.values())

    # Rule 1: Absolute minimum — no LEGO set is worth less than 5€
    MIN_PLAUSIBLE_PRICE = 5.0
    for source, price in list(market_prices.items()):
        if price < MIN_PLAUSIBLE_PRICE:
            outliers[source] = price
            warnings.append(
                f"{source} Preis ({price:.2f}€) unrealistisch niedrig — ignoriert"
            )

    # Remove absolute outliers first
    cleaned = {s: p for s, p in market_prices.items() if s not in outliers}

    if len(cleaned) < 2:
        return cleaned, outliers

    # Rule 2: Statistical outlier detection (>60% from median)
    prices_list = sorted(cleaned.values())
    median = prices_list[len(prices_list) // 2]

    if len(cleaned) >= 3:
        OUTLIER_THRESHOLD = 0.60
        for source, price in list(cleaned.items()):
            deviation = abs(price - median) / median if median > 0 else 0
            if deviation > OUTLIER_THRESHOLD:
                outliers[source] = price
                warnings.append(
                    f"{source} Preis ({price:.2f}€) weicht {deviation:.0%} vom Median "
                    f"({median:.2f}€) ab — als Ausreißer entfernt"
                )

    cleaned = {s: p for s, p in market_prices.items() if s not in outliers}
    return cleaned, outliers


def calculate_consensus(prices: list[ScrapedPrice]) -> MarketConsensus:
    """Calculate weighted consensus price from multiple sources.

    Logic:
    1. Filter out obvious outliers
    2. If all sources agree (±10%): Use median
    3. If large divergence (>20%): Use weighted average with warnings
    4. If only 1-2 sources: Conservative estimate with warning
    """
    # Filter to sources that have weight > 0
    raw_prices = {}
    for p in prices:
        if p.source in SOURCE_WEIGHTS and p.is_reliable and p.price_eur > 0:
            # For eBay, prefer median_price if available
            price = p.median_price if p.median_price and p.source == "EBAY_SOLD" else p.price_eur
            raw_prices[p.source] = price

    warnings: list[str] = []

    # Remove outliers before consensus calculation
    market_prices, outliers = _remove_outliers(raw_prices, warnings)

    result = MarketConsensus(
        consensus_price=0.0,
        num_sources=len(market_prices),
        source_prices=market_prices,
        outliers_removed=outliers,
        warnings=warnings,
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
        source = list(market_prices.keys())[0]
        result.consensus_price = prices_list[0]
        result.is_reliable = False
        result.warnings.append(f"Nur 1 Datenquelle ({source}) — unsichere Datenlage!")
        return result

    # ── Case 2: Sources agree (±10%) → Use median ───────
    median = sorted(prices_list)[len(prices_list) // 2]
    if result.divergence_percent <= 0.10:
        result.consensus_price = median
        return result

    # ── Case 3: Divergence → Weighted average ────────────
    if result.divergence_percent > settings.price_divergence_warning:
        result.warnings.append(
            f"Preisabweichung zwischen Quellen: {result.divergence_percent:.0%}. "
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
