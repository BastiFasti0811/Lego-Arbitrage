"""Market Consensus Engine - aggregates prices from multiple sources."""


from dataclasses import dataclass, field

import structlog

from app.config import settings
from app.scrapers.base import ScrapedPrice

logger = structlog.get_logger()


def is_persistable_consensus(consensus: "MarketConsensus") -> bool:
    """Whether a consensus is solid enough to store as a set's market price.

    Two independent sources that broadly agree. A single source is a guess,
    and extreme divergence means we do not know which source to believe.

    Note on the bound: divergence is (high - low) / mean, so 0.30 admits two
    sources up to a factor of ~1.35 apart, not 1.30.
    """
    return (
        consensus.consensus_price > 0
        and consensus.num_sources >= 2
        and consensus.divergence_percent <= 0.30
    )


def _median(values: list[float]) -> float:
    """True median. Taking the upper of two values drifted every two-source
    consensus upwards — and with three price sources that is the common case.
    """
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return 0.0
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2

ZERO_WEIGHT_SOURCES = {
    "AMAZON": 0.0,
    "KLEINANZEIGEN": 0.0,
    "LEGO_COM": 0.0,
}

# Quellen, die den Konsens gar nicht erst erreichen — nicht im Median, nicht in
# der Quellenzahl. Sie stehen bewusst NICHT in ZERO_WEIGHT_SOURCES: ein Gewicht
# von 0.0 laesst die Quelle mitzaehlen und wirkt erst im gewichteten Mittel.
#
# EBAY_ACTIVE ist der Median offener Angebote. Der Scraper greift darauf
# zurueck, sobald die Sold-Suche nichts liefert — ob wegen des 403, wegen eines
# geschluckten Fehlers oder weil zu diesem Set schlicht nichts verkauft wurde.
# In allen drei Faellen misst der Wert Forderungen, nicht Verkaeufe, und darf
# die Geldzahlen des gehaltenen Bestands nicht mittragen.
#
# Die Quelle zaehlte frueher mit, und das war richtig, solange BrickEconomy
# nichts lieferte — sie war die einzige verfuegbare zweite Quelle. Seit
# BrickEconomy wieder antwortet (22 von 41 Sets im Lauf vom 31.08.2026), traegt
# diese Begruendung nicht mehr: der Ausbau kostet gemessen ein einziges Set.
EXCLUDED_FROM_CONSENSUS = frozenset({"EBAY_ACTIVE"})


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
    """Return runtime-configurable consensus source weights.

    Membership in this table decides whether a source reaches the consensus at
    all — `calculate_consensus` keeps exactly the prices whose source is a key
    here. Ein Gewicht von 0.0 schliesst deshalb nichts aus: so eine Quelle
    zaehlt weiterhin fuer `num_sources` und den Median, und nur das gewichtete
    Mittel ignoriert sie. Wer wirklich draussen sein soll, darf hier nicht
    auftauchen — siehe EBAY_ACTIVE.
    """
    weights = {
        "EBAY_SOLD": settings.weight_ebay_sold,
        "BRICKECONOMY": settings.weight_brickeconomy,
        "IDEALO": settings.weight_idealo,
        "BRICKMERGE": settings.weight_brickmerge,
        **ZERO_WEIGHT_SOURCES,
    }
    # Der Filter ist die Durchsetzung, nicht das Weglassen oben: wer eine
    # ausgeschlossene Quelle spaeter wieder eintraegt, wird hier ueberstimmt,
    # statt sie unbemerkt zurueck in die Geldzahlen zu holen.
    return {src: weight for src, weight in weights.items() if src not in EXCLUDED_FROM_CONSENSUS}


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
    median = _median(prices_list)

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
    unreliable_sources: list[str] = []
    for scraped_price in prices:
        # is_reliable ist ein Qualitaetsmerkmal, kein Ausschlusskriterium: der
        # EBAY_ACTIVE-Fallback ist per Definition unsicher und waere sonst
        # trotz eigenem Gewicht nie im Konsens gelandet.
        if scraped_price.source in source_weights and scraped_price.price_eur > 0:
            if not scraped_price.is_reliable:
                unreliable_sources.append(scraped_price.source)
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

    if unreliable_sources:
        result.is_reliable = False
        result.warnings.append(
            "Unsichere Quelle(n) im Konsens: " + ", ".join(sorted(set(unreliable_sources)))
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

    median = _median(prices_list)
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
