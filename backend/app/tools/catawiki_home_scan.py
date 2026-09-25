"""Catawiki-Scan auf dem Heimrechner (Prod ist bei Akamai gesperrt).

Fragt Prod, ob ein Scan faellig ist (Button in der App oder Zeitplan), liest
die Lose ueber den eigenen Internetanschluss und liefert sie an Prod ab.
Bewertung, Speicherung und Telegram macht Prod.

Aufruf aus dem Repo-Wurzelverzeichnis:

    PYTHONPATH=backend backend/.venv/Scripts/python.exe -m app.tools.catawiki_home_scan
    ... --force     # scannen, auch wenn nichts angefordert ist
    ... --dry-run   # scannen und ausgeben, nichts an Prod schicken

Konfiguration (Umgebung oder Datei %USERPROFILE%\\.lego-arbitrage\\home-scan.env,
Zeilen KEY=VALUE):

    LEGO_API_URL=<Adresse der App wie im Browser, mit Basispfad, z. B. https://example.de/lego>
    LEGO_REMOTE_SCAN_TOKEN=<derselbe Wert wie Einstellungen > Catawiki > Heimrechner-Token>

Regelmaessig starten: scripts/install-catawiki-home-scan.ps1 legt eine
Windows-Aufgabe an, die das alle 10 Minuten tut. Ohne Auftrag endet ein Lauf
nach einer Anfrage.
"""

import argparse
import asyncio
import os
import sys
from dataclasses import asdict
from pathlib import Path

import httpx

from app.services.catawiki import CatawikiParseError, CatawikiScraper, needs_lot_details

RUNNER_VERSION = "1"
CONFIG_FILE = Path.home() / ".lego-arbitrage" / "home-scan.env"
# Nach so vielen gesperrten Losabrufen hintereinander aufhoeren statt weiterzuhaemmern.
MAX_CONSECUTIVE_BLOCKS = 3


def load_config() -> tuple[str, str]:
    values = {}
    if CONFIG_FILE.exists():
        # utf-8-sig: PowerShell 5 schreibt die Datei mit BOM.
        for line in CONFIG_FILE.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                values[key.strip()] = value.strip()
    api_url = os.environ.get("LEGO_API_URL") or values.get("LEGO_API_URL")
    token = os.environ.get("LEGO_REMOTE_SCAN_TOKEN") or values.get("LEGO_REMOTE_SCAN_TOKEN")
    if not api_url or not token:
        raise SystemExit(f"LEGO_API_URL und LEGO_REMOTE_SCAN_TOKEN fehlen (Umgebung oder {CONFIG_FILE}).")
    return api_url.rstrip("/"), token


async def scan(job: dict) -> tuple[list[dict], list[str]]:
    """Alle Scan-URLs lesen; Details nur fuer Lose, die laut Liste frei werden koennen."""
    lots: list[dict] = []
    errors: list[str] = []
    async with CatawikiScraper(cookie_header=job.get("cookie_header"), user_agent=job.get("user_agent")) as scraper:
        for category_url in job["scan_urls"]:
            try:
                listed = await scraper.scan_category(category_url, limit=job.get("max_results_per_url", 20))
            except (httpx.HTTPError, CatawikiParseError) as exc:
                errors.append(f"Heimrechner: {category_url[:80]} nicht lesbar ({type(exc).__name__})")
                continue
            print(f"{category_url}: {len(listed)} Lose")
            blocked = 0
            for lot in listed:
                if needs_lot_details(lot) and blocked < MAX_CONSECUTIVE_BLOCKS:
                    try:
                        lot = await scraper.get_lot(lot.url)
                        blocked = 0
                    except (httpx.HTTPError, CatawikiParseError) as exc:
                        blocked += 1
                        errors.append(f"Heimrechner: Los {lot.lot_id} nicht lesbar ({type(exc).__name__})")
                        if blocked == MAX_CONSECUTIVE_BLOCKS:
                            errors.append("Heimrechner: mehrfach gesperrt, restliche Lose ohne Details")
                entry = asdict(lot)
                entry["category_url"] = category_url
                for unused in ("seller_location", "auction_end_label"):
                    entry.pop(unused, None)
                entry["set_numbers"] = entry.get("set_numbers") or []
                lots.append(entry)
    return lots, errors[:20]


async def run(force: bool, dry_run: bool) -> int:
    api_url, token = load_config()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=api_url, headers=headers, timeout=60) as api:
        response = await api.get("/api/remote-scan/runner/job")
        response.raise_for_status()
        job = response.json()
        if not job["run"] and not force:
            return 0
        if not job["scan_urls"]:
            if not force:
                return 0
            # --force ohne Auftrag: die URLs stehen nur in Prods Einstellungen.
            print("Keine Scan-URLs im Auftrag (Einstellungen > Catawiki > Scan URLs).")
            return 1
        print(f"Scan ({job['reason'] or 'erzwungen'}): {len(job['scan_urls'])} URL(s)")
        lots, errors = await scan(job)
        verified = sum(1 for lot in lots if lot["details_verified"])
        print(f"{len(lots)} Lose, davon {verified} mit Details, {len(errors)} Fehler")
        if dry_run:
            for lot in lots:
                print(f"  {lot['lot_id']} {lot['title'][:50]} | {lot['current_bid']} | {lot['condition']}")
            return 0
        response = await api.post("/api/remote-scan/runner/results", json={
            "lots": lots, "errors": errors, "runner_version": RUNNER_VERSION,
        })
        response.raise_for_status()
        print(f"An Prod uebergeben: {response.json()['accepted']} Lose")
    return 0


def _log_to_file_without_console() -> None:
    # Die Windows-Aufgabe startet pythonw (kein Fenster alle 10 Minuten) -- dort
    # gibt es kein stdout. Ausgaben landen dann neben der Konfiguration.
    if sys.stdout is None or sys.stderr is None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log = open(CONFIG_FILE.with_name("home-scan.log"), "a", encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stderr = log


def main() -> None:
    _log_to_file_without_console()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force", action="store_true", help="scannen, auch ohne Anforderung")
    parser.add_argument("--dry-run", action="store_true", help="nichts an Prod schicken")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(run(args.force, args.dry_run)))
    except httpx.HTTPStatusError as exc:
        hint = " (Token pruefen)" if exc.response.status_code == 401 else ""
        raise SystemExit(f"Prod antwortet {exc.response.status_code}{hint}") from exc


if __name__ == "__main__":
    main()
