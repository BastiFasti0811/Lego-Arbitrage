from app.models import InventoryItem, LegoSet


def test_market_price_timestamps_are_timezone_aware():
    # scrape_daily and deal_analysis write datetime.now(UTC) into these columns;
    # asyncpg rejects aware values for TIMESTAMP WITHOUT TIME ZONE and the whole
    # per-set transaction (prices + offers) rolls back.
    assert LegoSet.__table__.c.market_price_updated_at.type.timezone
    assert InventoryItem.__table__.c.market_price_updated_at.type.timezone
