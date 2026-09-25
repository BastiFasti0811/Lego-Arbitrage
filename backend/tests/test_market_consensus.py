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
    # Abweichung, aber BrickMerge liegt hoeher (EOL-Haendlerpreis): kein Rueckfall.
    assert resolve_market_price(consensus(("EBAY_SOLD", 350.0), ("BRICKMERGE", 540.0))) is None
    # Einzelquelle ohne BrickMerge: kein Preis.
    assert resolve_market_price(consensus(("BRICKECONOMY", 90.0))) is None


def test_price_basis_from_sources_matches_the_persist_rule():
    from app.engine.market_consensus import price_basis_from_sources

    assert price_basis_from_sources({"EBAY_SOLD": 100.0, "BRICKMERGE": 105.0}) == "CONSENSUS"
    assert price_basis_from_sources({"BRICKMERGE": 114.99}) == "BRICKMERGE_ONLY"
    # Abweichung >30 %: BrickMerge nur als niedrigster Wert.
    assert price_basis_from_sources({"EBAY_SOLD": 200.0, "BRICKMERGE": 120.0}) == "BRICKMERGE_ONLY"
    assert price_basis_from_sources({"EBAY_SOLD": 350.0, "BRICKMERGE": 540.0}) is None
    assert price_basis_from_sources({"BRICKECONOMY": 90.0}) is None
    assert price_basis_from_sources({}) is None


def test_deal_checker_marks_brickmerge_only_when_it_shows_the_brickmerge_price():
    from app.api.routes.analysis import AnalysisResponse

    base = {
        "set_number": "1", "set_name": "x", "release_year": 2020, "theme": "t", "set_age": 1, "category": "c",
        "uvp": None, "offer_price": 1.0, "discount_vs_uvp": None, "num_sources": 1, "roi_percent": 0.0,
        "annualized_roi": 0.0, "net_profit": 0.0, "total_purchase_cost": 1.0, "total_selling_costs": 0.0,
        "risk_score": 0, "risk_rating": "LOW", "recommendation": "X", "reason": "", "suggestions": [],
        "opportunity_score": 0.0, "confidence": 1.0, "warnings": [], "analyzed_at": "2026-09-25T00:00:00",
    }
    only_bm = AnalysisResponse(**base, market_price=300.0, source_prices={"BRICKMERGE": 300.0})
    divergent = AnalysisResponse(**base, market_price=400.0, source_prices={"EBAY_SOLD": 500.0, "BRICKMERGE": 300.0})
    assert only_bm.price_basis == "BRICKMERGE_ONLY"
    # Verdikt rechnet mit 400 (Konsens), nicht mit BrickMerge: keine gelbe "nur BrickMerge"-Zahl.
    assert divergent.price_basis is None
