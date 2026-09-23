from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import auctions
from app.services import auction_tracking
from app.services.catawiki import CatawikiLotCandidate
from app.tasks import catawiki_scan

URL = "https://www.catawiki.com/de/l/102824557"
# Catawiki-Scan ist ohne Einstellung aus; die Task-Tests schalten ihn ein.
SCAN_ON = {"catawiki_scan_urls": URL, "catawiki_scan_frequency": "daily"}
TELEGRAM = {"telegram_bot_token": "t", "telegram_chat_id": "c"}


def lot(**changes):
    return CatawikiLotCandidate(**{
        "lot_id": "102824557", "url": URL, "title": "LEGO 10282 versiegelt",
        "set_numbers": ["10282"], "current_bid": 125, "shipping_eur": 13,
        "condition": "NEW_SEALED", "details_verified": True, **changes,
    })


@pytest.mark.parametrize("changes", [
    {"shipping_eur": None}, {"current_bid": None}, {"is_closed": True},
    {"condition": "USED_COMPLETE"}, {"set_numbers": ["10282", "75313"]},
    {"title": "LED Beleuchtung fuer LEGO 10282"},
])
async def test_unsafe_lots_remain_visible_without_bid_recommendation(monkeypatch, changes):
    evaluate = AsyncMock()
    monkeypatch.setattr(auctions, "evaluate_auction", evaluate)
    result = await auctions._evaluate_lot(category_url=URL, platform="CATAWIKI", lot=lot(**changes))
    assert result is not None and not result.can_bid_now
    assert result.recommended_max_bid is None and result.recommendation_text
    evaluate.assert_not_awaited()


async def test_discovery_fetches_details_even_when_card_has_a_bid(monkeypatch):
    details = AsyncMock(return_value=lot(current_bid=150, shipping_eur=29))
    class Scraper:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        scan_category = AsyncMock(return_value=[lot(current_bid=10, shipping_eur=None)])
        get_lot = details
    evaluate = AsyncMock(return_value="evaluated")
    monkeypatch.setattr(auctions, "_make_scraper", lambda *args: Scraper())
    monkeypatch.setattr(auctions, "validate_marketplace_url", lambda *args: None)
    monkeypatch.setattr(auctions, "_evaluate_lot", evaluate)
    await auctions._discover_for_category(
        category_url="https://www.catawiki.com/de/c/708-lego", source_platform="CATAWIKI",
        cookie_header=None, user_agent=None, max_results=20,
    )
    assert evaluate.call_args.kwargs["lot"].current_bid == 150
    assert evaluate.call_args.kwargs["lot"].shipping_eur == 29
    details.assert_awaited_once_with(URL)


async def test_blocked_scan_persists_failure_before_reporting_it(monkeypatch):
    monkeypatch.setattr(auctions, "validate_marketplace_url", lambda *args: None)
    monkeypatch.setattr(auctions, "_discover_for_category", AsyncMock(side_effect=httpx.ConnectError("blocked")))
    save = AsyncMock()
    monkeypatch.setattr(auctions, "save_scan", save)
    with pytest.raises(HTTPException) as exc:
        await auctions._scan_urls("CATAWIKI", [URL], None, None, 20)
    assert exc.value.status_code == 502
    assert save.call_args.args[2]


def test_invalid_limits_and_negative_fees_rejected():
    with pytest.raises(ValidationError):
        auctions.AuctionDiscoverRequest(max_results_per_url=10000)
    with pytest.raises(ValidationError):
        auctions.AuctionWatchCreate(set_number="10282", source_url=URL, current_bid=10, buyer_fee_rate=-1)


def test_weekly_scan_uses_berlin_day_and_supports_off():
    # 22:40 UTC on Saturday is already Sunday in Berlin.
    saturday = datetime(2026, 9, 5, 22, 40, tzinfo=UTC)
    assert catawiki_scan.catawiki_scan_due("weekly", saturday)
    assert not catawiki_scan.catawiki_scan_due("weekly", datetime(2026, 9, 5, 8, tzinfo=UTC))
    assert not catawiki_scan.catawiki_scan_due("off", saturday)
    # Ohne Einstellung aus, bis der Parser gegen echte Los-Seiten verifiziert ist.
    assert not catawiki_scan.catawiki_scan_due(None, saturday)


async def test_scheduled_source_failure_cannot_mark_heartbeat_success(monkeypatch):
    monkeypatch.setattr(catawiki_scan, "SUPPORTED_DISCOVERY_PLATFORMS", ("CATAWIKI",))
    monkeypatch.setattr(catawiki_scan, "get_settings_map", AsyncMock(return_value=SCAN_ON))
    monkeypatch.setattr(catawiki_scan, "_discover_configured_platform", AsyncMock(side_effect=RuntimeError("blocked")))
    with pytest.raises(RuntimeError, match="fehlgeschlagen"):
        await catawiki_scan._scan_configured_categories_async()


async def test_no_config_reports_skipped_without_fetching(monkeypatch):
    monkeypatch.setattr(catawiki_scan, "get_settings_map", AsyncMock(return_value={}))
    discover = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "_discover_configured_platform", discover)
    result = await catawiki_scan._scan_configured_categories_async()
    assert result["platforms"] == 0 and len(result["skipped"]) == 3
    discover.assert_not_awaited()


async def test_worker_time_limit_is_never_swallowed(monkeypatch):
    monkeypatch.setattr(catawiki_scan, "SUPPORTED_DISCOVERY_PLATFORMS", ("CATAWIKI",))
    monkeypatch.setattr(catawiki_scan, "get_settings_map", AsyncMock(return_value=SCAN_ON))
    monkeypatch.setattr(catawiki_scan, "_discover_configured_platform", AsyncMock(side_effect=SoftTimeLimitExceeded()))
    with pytest.raises(SoftTimeLimitExceeded):
        await catawiki_scan._scan_configured_categories_async()


async def test_scan_timeout_preserves_completed_lots_and_reports_failure(monkeypatch):
    monkeypatch.setattr(auctions, "validate_marketplace_url", lambda *args: None)
    result = auctions.AuctionDiscoverResult(
        source_platform="CATAWIKI", category_url=URL, lot_title="LEGO", source_url=URL,
    )
    async def partial(**kwargs):
        kwargs["collected"][URL] = result
        raise TimeoutError()
    monkeypatch.setattr(auctions, "_discover_for_category", partial)
    save = AsyncMock()
    monkeypatch.setattr(auctions, "save_scan", save)
    with pytest.raises(HTTPException, match="Zeitlimit"):
        await auctions._scan_urls("CATAWIKI", [URL], None, None, 20)
    assert save.call_args.args[1] == [result.model_dump()]


async def test_watch_refresh_failure_clears_old_buy_signal(monkeypatch):
    item = SimpleNamespace(source_platform="CATAWIKI", source_url=URL, check_count=0, max_bid=200)
    monkeypatch.setattr(auction_tracking, "get_settings_map", AsyncMock(return_value={}))
    monkeypatch.setattr(auction_tracking.CatawikiScraper, "get_lot", AsyncMock(side_effect=RuntimeError("403")))
    with pytest.raises(RuntimeError):
        await auction_tracking.refresh_watch_item(item, SimpleNamespace(set_number="10282"))
    assert item.status == "NEEDS_REVIEW" and item.max_bid is None
    assert item.all_in_cost_current is None


async def test_watch_refresh_reads_new_bid_and_ends_closed_lot(monkeypatch):
    item = SimpleNamespace(source_platform="CATAWIKI", source_url=URL, check_count=0, current_bid=10)
    monkeypatch.setattr(auction_tracking, "get_settings_map", AsyncMock(return_value={}))
    monkeypatch.setattr(auction_tracking.CatawikiScraper, "get_lot", AsyncMock(return_value=lot(is_closed=True)))
    assert not await auction_tracking.refresh_watch_item(item, SimpleNamespace(set_number="10282"))
    assert item.current_bid == 125 and item.purchase_shipping == 13
    assert item.status == "ENDED" and item.max_bid is None


def _result(url, can_bid_now=True):
    return auctions.AuctionDiscoverResult(
        source_platform="CATAWIKI", category_url=URL, lot_title="LEGO 10282", source_url=url,
        set_number="10282", current_bid=100, recommended_max_bid=150, can_bid_now=can_bid_now,
    )


async def test_partial_scan_after_timeout_still_notifies_then_fails(monkeypatch):
    # Zeitlimit nach dem ersten Los: der Fund ist gespeichert und muss gemeldet
    # werden, der Task endet trotzdem rot.
    monkeypatch.setattr(catawiki_scan, "SUPPORTED_DISCOVERY_PLATFORMS", ("CATAWIKI",))
    monkeypatch.setattr(catawiki_scan, "get_settings_map", AsyncMock(return_value={**SCAN_ON, **TELEGRAM}))
    found = _result(URL)
    monkeypatch.setattr(catawiki_scan, "_discover_configured_platform",
                        AsyncMock(return_value=([found], ["CATAWIKI: Zeitlimit erreicht."])))
    monkeypatch.setattr(catawiki_scan, "unnotified_results", AsyncMock(return_value=[found.model_dump()]))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(catawiki_scan, "send_auction_discovery_summary", send)
    mark = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "mark_notified", mark)

    with pytest.raises(RuntimeError, match="Zeitlimit"):
        await catawiki_scan._scan_configured_categories_async()

    send.assert_awaited_once()
    mark.assert_awaited_once_with("CATAWIKI", [URL])


async def test_missing_telegram_is_skipped_not_failed(monkeypatch):
    monkeypatch.setattr(catawiki_scan, "SUPPORTED_DISCOVERY_PLATFORMS", ("CATAWIKI",))
    monkeypatch.setattr(catawiki_scan, "get_settings_map", AsyncMock(return_value=SCAN_ON))
    found = _result(URL)
    monkeypatch.setattr(catawiki_scan, "_discover_configured_platform", AsyncMock(return_value=([found], [])))
    monkeypatch.setattr(catawiki_scan, "unnotified_results", AsyncMock(return_value=[found.model_dump()]))
    send = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "send_auction_discovery_summary", send)
    mark = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "mark_notified", mark)

    summary = await catawiki_scan._scan_configured_categories_async()

    assert summary["errors"] == []
    assert any("Telegram nicht konfiguriert" in entry for entry in summary["skipped"])
    send.assert_not_awaited()
    # Nicht als gemeldet markieren: sobald Telegram steht, kommen die Treffer noch.
    mark.assert_not_awaited()


async def test_scan_without_frequency_setting_does_not_fetch(monkeypatch):
    monkeypatch.setattr(catawiki_scan, "SUPPORTED_DISCOVERY_PLATFORMS", ("CATAWIKI",))
    monkeypatch.setattr(catawiki_scan, "get_settings_map", AsyncMock(return_value={"catawiki_scan_urls": URL}))
    discover = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "_discover_configured_platform", discover)
    summary = await catawiki_scan._scan_configured_categories_async()
    assert "CATAWIKI: heute nicht geplant" in summary["skipped"]
    discover.assert_not_awaited()


async def test_collect_scan_returns_partial_results_instead_of_raising(monkeypatch):
    monkeypatch.setattr(auctions, "validate_marketplace_url", lambda *args: None)
    found = _result(URL)

    async def partial(**kwargs):
        kwargs["collected"][URL] = found
        raise TimeoutError()

    monkeypatch.setattr(auctions, "_discover_for_category", partial)
    monkeypatch.setattr(auctions, "save_scan", AsyncMock())
    results, errors = await auctions._collect_scan("CATAWIKI", [URL], None, None, 20)
    assert results == [found]
    assert errors and "Zeitlimit" in errors[0]
