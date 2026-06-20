"""Market Consensus Engine - aggregates prices from multiple sources."""

from dataclasses import dataclass, field

import structlog

from app.config import settings
from app.scrapers.base import ScrapedPrice

logger = structlog.get_logger()

ZERO_WEIGHT_SOURCES = {
    "AMAZON": 0.0,
    "KLEINANZEIGEN": 0.0,
    "LEGO_COM": 0.0,
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
    divergence_percent: float = 0.0
    is_reliable: bool = True
    warnings: list[str] = field(default_factory=list)
    outliers_removed: dict[str, float] = field(default_factory=dict)


def _source_weights() -> dict[str, float]:
    """Return runtime-configurable consensus source weights."""
    return {
        "EBAY_SOLD": settings.weight_ebay_sold,
        "BRICKECONOMY": settings.weight_brickeconomy,
        "IDEALO": settings.weight_idealo,
        "BRICKMERGE": settings.weight_brickmerge,
        **ZERO_WEIGHT_SOURCES,
    }


def _remove_outliers(
    market_prices: dict[str, float],
    warnings: list[str],
) -> tuple[dict[str, float], dict[str, float]]:
    """Remove obvious outlier prices before consensus calculation."""
    outliers: dict[str, float] = {}

    if not market_prices:
        return market_prices, outliers

    min_plausible_price = 5.0
    for source, price in list(market_prices.items()):
        if price < min_plausible_price:
            outliers[source] = price
            warnings.append(f"{source} Preis ({price:.2f} EUR) unrealistisch niedrig - ignoriert")

    cleaned = {source: price for source, price in market_prices.items() if source not in outliers}
    if len(cleaned) < 2:
        return cleaned, outliers

    prices_list = sorted(cleaned.values())
    median = prices_list[len(prices_list) // 2]

    if len(cleaned) >= 3:
        outlier_threshold = 0.60
        for source, price in list(cleaned.items()):
            deviation = abs(price - median) / median if median > 0 else 0
            if deviation > outlier_threshold:
                outliers[source] = price
                warnings.append(
                    f"{source} Preis ({price:.2f} EUR) weicht {deviation:.0%} vom Median "
                    f"({median:.2f} EUR) ab - als Ausreisser entfernt"
                )

    cleaned = {source: price for source, price in market_prices.items() if source not in outliers}
    return cleaned, outliers


def calculate_consensus(prices: list[ScrapedPrice]) -> MarketConsensus:
    """Calculate weighted consensus price from multiple sources."""
    source_weights = _source_weights()
    raw_prices: dict[str, float] = {}
    for scraped_price in prices:
        if scraped_price.source in source_weights and scraped_price.is_reliable and scraped_price.price_eur > 0:
            price = (
                scraped_price.median_price
                if scraped_price.median_price and scraped_price.source == "EBAY_SOLD"
                else scraped_price.price_eur
            )
            raw_prices[scraped_price.source] = price

    warnings: list[str] = []
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
        result.warnings.append("Keine Marktpreisdaten verfuegbar!")
        return result

    prices_list = list(market_prices.values())
    result.price_range_low = min(prices_list)
    result.price_range_high = max(prices_list)

    if len(prices_list) >= 2:
        mean = sum(prices_list) / len(prices_list)
        if mean > 0:
            result.divergence_percent = (result.price_range_high - result.price_range_low) / mean

    if len(market_prices) == 1:
        source = list(market_prices.keys())[0]
        result.consensus_price = prices_list[0]
        result.is_reliable = False
        result.warnings.append(f"Nur 1 Datenquelle ({source}) - unsichere Datenlage!")
        return result

    median = sorted(prices_list)[len(prices_list) // 2]
    if result.divergence_percent <= 0.10:
        result.consensus_price = median
        return result

    if result.divergence_percent > settings.price_divergence_warning:
        result.warnings.append(
            f"Preisabweichung zwischen Quellen: {result.divergence_percent:.0%}. "
            "Gewichteter Durchschnitt wird verwendet."
        )

    total_weight = 0.0
    weighted_sum = 0.0
    for source, price in market_prices.items():
        weight = source_weights.get(source, 0.0)
        if weight > 0:
            weighted_sum += price * weight
            total_weight += weight
            result.weights_used[source] = weight

    result.consensus_price = round(weighted_sum / total_weight, 2) if total_weight > 0 else median

    if len(market_prices) < 2:
        result.is_reliable = False
    if result.divergence_percent > 0.30:
        result.is_reliable = False
        result.warnings.append("Extreme Preisabweichung >30% - manuell verifizieren!")

    return result
