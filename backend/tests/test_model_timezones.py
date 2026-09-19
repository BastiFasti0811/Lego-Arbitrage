from app.models import InventoryItem, LegoSet


def test_market_price_timestamps_are_timezone_aware():
    # scrape_daily and deal_analysis write datetime.now(UTC) into these columns;
    # asyncpg rejects aware values for TIMESTAMP WITHOUT TIME ZONE and the whole
    # per-set transaction (prices + offers) rolls back.
    assert LegoSet.__table__.c.market_price_updated_at.type.timezone
    assert InventoryItem.__table__.c.market_price_updated_at.type.timezone


def test_ai_analysis_at_is_timezone_aware():
    # Gleiches Bugmuster wie market_price_updated_at (siehe Migration
    # c4f2a91b7d3e): schreibt die KI-Fotoanalyse (PR 2) datetime.now(UTC) in
    # eine TIMESTAMP WITHOUT TIME ZONE-Spalte, lehnt asyncpg den Wert ab und
    # die Transaktion rollt zurueck.
    assert InventoryItem.__table__.c.ai_analysis_at.type.timezone
