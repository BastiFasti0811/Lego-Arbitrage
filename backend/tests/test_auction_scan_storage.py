import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models.auction_scan import AuctionScanState
from app.services import auction_scan_state
from app.tasks import catawiki_scan


@pytest.fixture(autouse=True)
def _server_scans_catawiki(monkeypatch):
    # Diese Tests pruefen die Mechanik des Server-Scans am Beispiel Catawiki.
    # Im Betrieb scannt Catawiki der Heimrechner (HOME_RUNNER_PLATFORMS).
    monkeypatch.setattr(catawiki_scan, "HOME_RUNNER_PLATFORMS", frozenset())

def test_scan_migration_matches_model_and_can_rollback():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/a91c07e54b22_auction_scan_states.py"
    spec = importlib.util.spec_from_file_location("auction_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with create_engine("sqlite://").begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        columns = {c["name"] for c in inspect(connection).get_columns("auction_scan_states")}
        assert columns == set(AuctionScanState.__table__.columns.keys())
        assert AuctionScanState.__table__.c.scanned_at.type.timezone
        module.downgrade()
        assert "auction_scan_states" not in inspect(connection).get_table_names()


@pytest.fixture
def storage(monkeypatch):
    engine = create_engine("sqlite://")
    AuctionScanState.__table__.create(engine)
    session = Session(engine, expire_on_commit=False)
    class AsyncSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def execute(self, statement):
            return session.execute(statement)
        async def commit(self):
            session.commit()
    monkeypatch.setattr(auction_scan_state, "async_session", AsyncSession)
    monkeypatch.setattr(auction_scan_state, "insert", insert)
    yield session
    session.close()
    engine.dispose()


async def test_new_scans_preserve_notification_history_and_errors(storage):
    result = {"source_url": "https://www.catawiki.com/de/l/12345678", "can_bid_now": True}
    await auction_scan_state.save_scan("CATAWIKI", [result], [])
    assert await auction_scan_state.unnotified_results("CATAWIKI", [result]) == [result]
    await auction_scan_state.mark_notified("CATAWIKI", [result["source_url"]])
    await auction_scan_state.save_scan("CATAWIKI", [result], [])
    storage.expire_all()
    assert await auction_scan_state.unnotified_results("CATAWIKI", [result]) == []
    await auction_scan_state.save_scan("CATAWIKI", [], ["Zugriff gesperrt"])
    storage.expire_all()
    state = storage.execute(select(AuctionScanState)).scalar_one()
    assert state.status == "FAILED" and state.errors == ["Zugriff gesperrt"]
    assert state.notified_urls == [result["source_url"]]


async def test_failed_notification_never_marks_lots_delivered(monkeypatch):
    monkeypatch.setattr(catawiki_scan, "SUPPORTED_DISCOVERY_PLATFORMS", ("CATAWIKI",))
    monkeypatch.setattr(catawiki_scan, "get_settings_map", AsyncMock(return_value={
        "catawiki_scan_urls": "configured", "catawiki_scan_frequency": "daily",
        "telegram_bot_token": "t", "telegram_chat_id": "c",
    }))
    monkeypatch.setattr(catawiki_scan, "_discover_configured_platform", AsyncMock(return_value=([], [])))
    monkeypatch.setattr(catawiki_scan, "unnotified_results", AsyncMock(return_value=[{"source_url": "lot"}]))
    monkeypatch.setattr(catawiki_scan, "send_auction_discovery_summary", AsyncMock(return_value=False))
    mark = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "mark_notified", mark)
    with pytest.raises(RuntimeError, match="Benachrichtigung"):
        await catawiki_scan._scan_configured_categories_async()
    mark.assert_not_awaited()
