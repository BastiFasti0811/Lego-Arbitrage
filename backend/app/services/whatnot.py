"""Helpers for scanning Whatnot listing pages and search/category pages."""

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper

logger = structlog.get_logger()

WHATNOT_BASE = "https://www.whatnot.com"


@dataclass
class WhatnotLotCandidate:
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
    normalized = text.replace("\xa0", " ").replace("EUR", "").replace("€", "").replace("$", "").strip()
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


def _extract_current_price(text: str | None) -> float | None:
    if not text:
        return None
    for pattern in (
        r"Current bid\s*[:\-]?\s*(?:EUR|€|\$)?\s*(\d[\d.,]*)",
        r"Starting bid\s*[:\-]?\s*(?:EUR|€|\$)?\s*(\d[\d.,]*)",
        r"Buy now\s*[:\-]?\s*(?:EUR|€|\$)?\s*(\d[\d.,]*)",
        r"Price\s*[:\-]?\s*(?:EUR|€|\$)?\s*(\d[\d.,]*)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _parse_money(match.group(1))
    return _parse_money(text)


def _extract_next_json(html: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    for script in soup.select("script"):
        content = script.string or script.get_text()
        if "__NEXT_DATA__" in (script.get("id") or ""):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return None
    return None


def parse_category_page(html: str, source_url: str) -> list[WhatnotLotCandidate]:
    """Extract Whatnot listing links from a category or search page."""
    soup = BeautifulSoup(html, "lxml")
    candidates: list[WhatnotLotCandidate] = []
    seen_urls: set[str] = set()

    for anchor in soup.select("a[href*='/listing/']"):
        href = anchor.get("href") or ""
        full_url = href if href.startswith("http") else urljoin(WHATNOT_BASE, href)
        if full_url in seen_urls:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title or "lego" not in title.lower():
            continue
        listing_match = re.search(r"/listing/([^/?#]+)", full_url)
        if not listing_match:
            continue
        container = anchor.find_parent(["article", "div", "li"]) or anchor
        text = container.get_text(" ", strip=True)
        candidates.append(
            WhatnotLotCandidate(
                lot_id=listing_match.group(1),
                title=title,
                url=full_url,
                current_bid=_extract_current_price(text),
                set_numbers=_extract_set_numbers(title),
            )
        )
        seen_urls.add(full_url)

    if candidates:
        return candidates

    payload = _extract_next_json(html)
    if not payload:
        return []

    serialized = json.dumps(payload)
    for listing_id, title in re.findall(
        r'"id":"([^"]+)".{0,240}?"title":"([^"]+LEGO[^"]*)"',
        serialized,
        re.IGNORECASE,
    ):
        full_url = f"{WHATNOT_BASE}/listing/{listing_id}"
        if full_url in seen_urls:
            continue
        candidates.append(
            WhatnotLotCandidate(
                lot_id=listing_id,
                title=title,
                url=full_url,
                set_numbers=_extract_set_numbers(title),
            )
        )
        seen_urls.add(full_url)

    return candidates


def parse_listing_page(html: str, url: str) -> WhatnotLotCandidate:
    """Extract details from a Whatnot listing page."""
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("h1")
    title = title_el.get_text(" ", strip=True) if title_el else ""
    if not title:
        meta_title = soup.select_one("meta[property='og:title']")
        title = meta_title.get("content", "").strip() if meta_title else ""

    body_text = soup.get_text(" ", strip=True)
    listing_match = re.search(r"/listing/([^/?#]+)", url)
    shipping = None
    shipping_match = re.search(
        r"Shipping\s*[:\-]?\s*(?:from\s*)?(?:EUR|€|\$)?\s*(\d[\d.,]*)",
        body_text,
        re.IGNORECASE,
    )
    if shipping_match:
        shipping = _parse_money(shipping_match.group(1))

    return WhatnotLotCandidate(
        lot_id=listing_match.group(1) if listing_match else "",
        title=title,
        url=url,
        current_bid=_extract_current_price(body_text),
        shipping_eur=shipping,
        set_numbers=_extract_set_numbers(title),
    )


class WhatnotScraper(BaseScraper):
    """Minimal Whatnot scraper with optional manual cookies."""

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
        client.headers["Referer"] = WHATNOT_BASE
        return client

    async def get_set_info(self, set_number: str):
        return None

    async def get_price(self, set_number: str):
        return None

    async def scan_category(self, category_url: str, limit: int = 30) -> list[WhatnotLotCandidate]:
        html = await self._fetch(category_url)
        return parse_category_page(html, category_url)[:limit]

    async def get_lot(self, lot_url: str) -> WhatnotLotCandidate:
        html = await self._fetch(lot_url)
        return parse_listing_page(html, lot_url)
