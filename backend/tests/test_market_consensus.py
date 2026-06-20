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

