"""Helpers for scanning Catawiki category pages and lot pages."""

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper

logger = structlog.get_logger()

CATAWIKI_BASE = "https://www.catawiki.com"


@dataclass
class CatawikiLotCandidate:
    lot_id: str
    title: str
    url: str
    current_bid: float | None = None
    shipping_eur: float | None = None
    seller_location: str | None = None
    auction_end_label: str | None = None
    set_numbers: list[str] | None = None


def _parse_money(text: str | None) -> float | None:
    if not text:
        return None
    normalized = text.replace("\xa0", " ").replace("EUR", "").replace("€", "").replace("â‚¬", "").strip()
    match = re.search(r"(\d[\d.,]*)", normalized)
    if not match:
        return None
    value = match.group(1)
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")
    return float(value)


def _extract_set_numbers(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\b(\d{4,6})\b", text or "")))


def _extract_current_bid(text: str | None) -> float | None:
    if not text:
        return None

    for pattern in (
        r"Aktuelles Gebot\s*[:\-]?\s*(\d[\d.,]*)",
        r"Current bid\s*[:\-]?\s*(\d[\d.,]*)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _parse_money(match.group(1))

    return _parse_money(text)


def _extract_next_json(html: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    script = soup.select_one("script#__NEXT_DATA__")
    if not script or not script.string:
        return None
    try:
        return json.loads(script.string)
    except json.JSONDecodeError:
        return None


def parse_category_page(html: str, source_url: str) -> list[CatawikiLotCandidate]:
    """Extract auction lots from a Catawiki category/result page."""
    soup = BeautifulSoup(html, "lxml")
    candidates: list[CatawikiLotCandidate] = []
    seen_urls: set[str] = set()

    for anchor in soup.select("a[href*='/l/']"):
        href = anchor.get("href") or ""
        if "/l/" not in href:
            continue
        full_url = href if href.startswith("http") else urljoin(CATAWIKI_BASE, href)
        if full_url in seen_urls:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title or "lego" not in title.lower():
            continue
        lot_match = re.search(r"/l/(\d+)", full_url)
        if not lot_match:
            continue

        container = anchor.find_parent(["article", "div", "li"]) or anchor
        text = container.get_text(" ", strip=True)
        current_bid = _extract_current_bid(text)
        set_numbers = _extract_set_numbers(title)
        candidates.append(
            CatawikiLotCandidate(
                lot_id=lot_match.group(1),
                title=title,
                url=full_url,
                current_bid=current_bid,
                set_numbers=set_numbers,
            )
        )
        seen_urls.add(full_url)

    if candidates:
        return candidates

    payload = _extract_next_json(html)
    if not payload:
        return []

    serialized = json.dumps(payload)
    for lot_id, title in re.findall(r'"id":"?(\d+)"?.{0,220}?"title":"([^"]+LEGO[^"]*)"', serialized, re.IGNORECASE):
        full_url = f"{CATAWIKI_BASE}/de/l/{lot_id}"
        if full_url in seen_urls:
            continue
        candidates.append(
            CatawikiLotCandidate(
                lot_id=lot_id,
                title=title,
                url=full_url,
                set_numbers=_extract_set_numbers(title),
            )
        )
        seen_urls.add(full_url)

    return candidates


def parse_lot_page(html: str, url: str) -> CatawikiLotCandidate:
    """Extract details from a specific Catawiki lot page."""
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("h1")
    title = title_el.get_text(" ", strip=True) if title_el else ""
    body_text = soup.get_text(" ", strip=True)
    lot_match = re.search(r"/l/(\d+)", url)

    shipping = None
    shipping_match = re.search(r"(\d[\d.,]*)\s*(?:€|â‚¬)\s+aus:", body_text, re.IGNORECASE)
    if shipping_match:
        shipping = _parse_money(shipping_match.group(1))

    current_bid = _extract_current_bid(body_text)

    return CatawikiLotCandidate(
        lot_id=lot_match.group(1) if lot_match else "",
        title=title,
        url=url,
        current_bid=current_bid,
        shipping_eur=shipping,
        set_numbers=_extract_set_numbers(title),
    )


class CatawikiScraper(BaseScraper):
    """Minimal Catawiki scraper that works with optional manual cookies."""

    def __init__(self, cookie_header: str | None = None, user_agent: str | None = None):
        super().__init__()
        self.cookie_header = cookie_header
        self.user_agent_override = user_agent

    async def _get_client(self):
        client = await super()._get_client()
        if self.cookie_header:
            client.headers["Cookie"] = self.cookie_header
        if self.user_agent_override:
            client.headers["User-Agent"] = self.user_agent_override
        client.headers["Referer"] = CATAWIKI_BASE
        return client

    async def get_set_info(self, set_number: str):
        return None

    async def get_price(self, set_number: str):
        return None

    async def scan_category(self, category_url: str, limit: int = 30) -> list[CatawikiLotCandidate]:
        html = await self._fetch(category_url)
        return parse_category_page(html, category_url)[:limit]

    async def get_lot(self, lot_url: str) -> CatawikiLotCandidate:
        html = await self._fetch(lot_url)
        return parse_lot_page(html, lot_url)
