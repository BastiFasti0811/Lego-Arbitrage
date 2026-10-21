"""Heimrechner-Skript: liest Lose und baut Nutzdaten, die Prod annimmt."""

import httpx
import pytest

from app.services.catawiki import PARSER_VERSION, CatawikiLotCandidate
from app.services.remote_scan import RemoteScanResults
from app.tools import catawiki_home_scan

AUCTION = "https://www.catawiki.com/de/a/1256209"


def _listed(lot_id: str, title: str, condition: str = "NEW_SEALED") -> CatawikiLotCandidate:
    return CatawikiLotCandidate(
        lot_id=lot_id, title=title, url=f"https://www.catawiki.com/de/l/{lot_id}",
        set_numbers=["10352"] if "10352" in title else [], condition=condition,
    )


class _FakeScraper:
    blocked: set[str] = set()

    def __init__(self, **kwargs):
        self.fetched = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def scan_category(self, url, limit):
        return [
            _listed("1", "Lego Set - 10352 - Krusty Burger"),
            _listed("2", "Lego Set - 76164 - Iron Man", condition="USED_COMPLETE"),
            _listed("3", "Lego Set - 10352 - Krusty Burger B"),
        ][:limit]

    async def get_lot(self, url):
        lot_id = url.rsplit("/", 1)[-1]
        if lot_id in self.blocked:
            raise httpx.HTTPStatusError("403", request=httpx.Request("GET", url),
                                        response=httpx.Response(403))
        lot = _listed(lot_id, "Lego Set - 10352 - Krusty Burger")
        lot.current_bid, lot.shipping_eur, lot.details_verified = 94.0, 12.0, True
        return lot


async def test_scan_builds_payload_that_prod_accepts(monkeypatch):
    monkeypatch.setattr(catawiki_home_scan, "CatawikiScraper", _FakeScraper)
    _FakeScraper.blocked = set()
    lots, errors = await catawiki_home_scan.scan({"scan_urls": [AUCTION], "max_results_per_url": 20})

    assert errors == []
    assert [lot["details_verified"] for lot in lots] == [True, False, True]  # gebraucht: ohne Details
    payload = RemoteScanResults.model_validate({
        "job_id": "a" * 32, "parser_version": PARSER_VERSION, "lots": lots, "errors": errors,
    })
    assert payload.lots[0].category_url == AUCTION and payload.lots[0].current_bid == 94.0


async def test_blocked_lot_is_reported_and_kept_without_details(monkeypatch):
    monkeypatch.setattr(catawiki_home_scan, "CatawikiScraper", _FakeScraper)
    _FakeScraper.blocked = {"1"}
    lots, errors = await catawiki_home_scan.scan({"scan_urls": [AUCTION], "max_results_per_url": 20})

    assert lots[0]["details_verified"] is False
    assert any("Los 1" in error for error in errors)


def test_config_file_with_bom(tmp_path, monkeypatch):
    config = tmp_path / "home-scan.env"
    config.write_text("﻿# Kommentar\nLEGO_API_URL=https://example.de/lego/\nLEGO_REMOTE_SCAN_TOKEN=abc\n",
                      encoding="utf-8")
    monkeypatch.setattr(catawiki_home_scan, "CONFIG_FILE", config)
    monkeypatch.delenv("LEGO_API_URL", raising=False)
    monkeypatch.delenv("LEGO_REMOTE_SCAN_TOKEN", raising=False)
    assert catawiki_home_scan.load_config() == ("https://example.de/lego", "abc")


def test_missing_config_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(catawiki_home_scan, "CONFIG_FILE", tmp_path / "fehlt.env")
    monkeypatch.delenv("LEGO_API_URL", raising=False)
    monkeypatch.delenv("LEGO_REMOTE_SCAN_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="LEGO_API_URL"):
        catawiki_home_scan.load_config()


async def test_structure_drift_keeps_the_scan_going(monkeypatch):
    """NOTES.md S4: ein AttributeError aus dem Parser darf nicht den ganzen Scan beenden."""

    class _DriftScraper(_FakeScraper):
        async def scan_category(self, url, limit):
            if url.endswith("/kaputt"):
                raise AttributeError("'list' object has no attribute 'get'")
            return await super().scan_category(url, limit)

        async def get_lot(self, url):
            if url.endswith("/1"):
                raise TypeError("unerwartete Struktur")
            return await super().get_lot(url)

    monkeypatch.setattr(catawiki_home_scan, "CatawikiScraper", _DriftScraper)
    _DriftScraper.blocked = set()
    lots, errors = await catawiki_home_scan.scan({
        "scan_urls": [f"{AUCTION}/kaputt", AUCTION], "max_results_per_url": 20,
    })

    assert [lot["lot_id"] for lot in lots] == ["1", "2", "3"]
    assert [lot["details_verified"] for lot in lots] == [False, False, True]  # Los 1 bleibt gesperrt
    assert any("kaputt" in error and "AttributeError" in error for error in errors)
    assert any("Los 1 nicht auswertbar (TypeError)" in error for error in errors)
