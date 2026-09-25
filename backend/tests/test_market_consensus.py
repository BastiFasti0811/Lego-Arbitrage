from app.engine.market_consensus import calculate_consensus
from app.scrapers.base import ScrapedPrice


def test_market_consensus_uses_runtime_weights(monkeypatch):
    monkeypatch.setattr("app.engine.market_consensus.settings.weight_ebay_sold", 1.0)
    monkeypatch.setattr("app.engine.market_consensus.settings.weight_brickmerge", 0.0)
    monkeypatch.setattr("app.engine.market_consensus.settings.weight_brickeconomy", 0.0)
    monkeypatch.setattr("app.engine.market_consensus.settings.weight_idealo", 0.0)

    consensus = calculate_consensus(
        [
            ScrapedPrice(source="EBAY_SOLD", price_eur=100.0),
            ScrapedPrice(source="BRICKMERGE", price_eur=200.0),
        ]
    )

    assert consensus.consensus_price == 100.0
    assert consensus.weights_used == {"EBAY_SOLD": 1.0}



def test_resolve_market_price_prefers_consensus_then_brickmerge():
    from app.engine.market_consensus import PriceBasis, resolve_market_price
    from app.scrapers.base import ScrapedPrice

    def consensus(*pairs):
        return calculate_consensus([ScrapedPrice(source=s, price_eur=p) for s, p in pairs])

    # Zwei passende Quellen: Konsens, gruen.
    assert resolve_market_price(consensus(("EBAY_SOLD", 100.0), ("BRICKMERGE", 105.0))) == (
        102.5, PriceBasis.CONSENSUS,
    )
    # Nur BrickMerge: BrickMerge, gelb.
    assert resolve_market_price(consensus(("BRICKMERGE", 114.99))) == (114.99, PriceBasis.BRICKMERGE_ONLY)
    # Zwei Quellen >30 % auseinander: kein Konsens, BrickMerge als Rueckfall.
    assert resolve_market_price(consensus(("EBAY_SOLD", 200.0), ("BRICKMERGE", 120.0))) == (
        120.0, PriceBasis.BRICKMERGE_ONLY,
    )
    # Einzelquelle ohne BrickMerge: kein Preis.
    assert resolve_market_price(consensus(("BRICKECONOMY", 90.0))) is None


def test_price_basis_from_sources_matches_the_persist_rule():
    from app.engine.market_consensus import price_basis_from_sources

    assert price_basis_from_sources({"EBAY_SOLD": 100.0, "BRICKMERGE": 105.0}) == "CONSENSUS"
    assert price_basis_from_sources({"BRICKMERGE": 114.99}) == "BRICKMERGE_ONLY"
    assert price_basis_from_sources({"EBAY_SOLD": 200.0, "BRICKMERGE": 120.0}) is None
    assert price_basis_from_sources({"BRICKECONOMY": 90.0}) is None
    assert price_basis_from_sources({}) is None
