"""Helpers for scanning BrickLink listing pages and result pages."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper

BRICKLINK_BASE = "https://www.bricklink.com"


@dataclass
class BrickLinkLotCandidate:
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
        r"(?:Price|EUR|€|\$)\s*[:\-]?\s*(\d[\d.,]*)",
        r"for\s*(\d[\d.,]*)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _parse_money(match.group(1))
    return _parse_money(text)


def _extract_set_number_from_url(url: str) -> list[str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    candidates: list[str] = []
    for key in ("S", "s", "P", "p"):
        values = query.get(key) or []
        for value in values:
            candidates.extend(_extract_set_numbers(value))
    return list(dict.fromkeys(candidates))


def parse_category_page(html: str, source_url: str) -> list[BrickLinkLotCandidate]:
    """Extract set offers from a BrickLink result page or store page."""
    soup = BeautifulSoup(html, "lxml")
    candidates: list[BrickLinkLotCandidate] = []
    seen_urls: set[str] = set()

    for anchor in soup.select("a[href*='catalogItem.page'], a[href*='catalogitem.page']"):
        href = anchor.get("href") or ""
        full_url = href if href.startswith("http") else urljoin(BRICKLINK_BASE, href)
        if full_url in seen_urls:
            continue
        title = anchor.get_text(" ", strip=True)
        set_numbers = _extract_set_numbers(title) or _extract_set_number_from_url(full_url)
        if not title and not set_numbers:
            continue
        container = anchor.find_parent(["tr", "article", "div", "li"]) or anchor
        text = container.get_text(" ", strip=True)
        candidates.append(
            BrickLinkLotCandidate(
                lot_id=set_numbers[0] if set_numbers else full_url,
                title=title or f"BrickLink {set_numbers[0]}" if set_numbers else "BrickLink listing",
                url=full_url,
                current_bid=_extract_current_price(text),
                set_numbers=set_numbers,
            )
        )
        seen_urls.add(full_url)

    return candidates


def parse_listing_page(html: str, url: str) -> BrickLinkLotCandidate:
    """Extract details from a BrickLink listing page."""
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("h1")
    title = title_el.get_text(" ", strip=True) if title_el else ""
    body_text = soup.get_text(" ", strip=True)
    set_numbers = _extract_set_numbers(title) or _extract_set_number_from_url(url)
    if not title and set_numbers:
        title = f"BrickLink {set_numbers[0]}"

    return BrickLinkLotCandidate(
        lot_id=set_numbers[0] if set_numbers else url,
        title=title,
        url=url,
        current_bid=_extract_current_price(body_text),
        set_numbers=set_numbers,
    )


class BrickLinkScraper(BaseScraper):
    """Minimal BrickLink scraper for listing and search pages."""

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
        client.headers["Referer"] = BRICKLINK_BASE
        return client

    async def get_set_info(self, set_number: str):
        return None

    async def get_price(self, set_number: str):
        return None

    async def scan_category(self, category_url: str, limit: int = 30) -> list[BrickLinkLotCandidate]:
        html = await self._fetch(category_url)
        return parse_category_page(html, category_url)[:limit]

    async def get_lot(self, lot_url: str) -> BrickLinkLotCandidate:
        html = await self._fetch(lot_url)
        return parse_listing_page(html, lot_url)
