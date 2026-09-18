"""Helpers for scanning Catawiki category pages and lot pages."""

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import structlog
from bs4 import BeautifulSoup

from app.domain.condition import classify_listing_condition
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
    condition: str = "UNKNOWN"
    box_damage: bool = False
    is_closed: bool = False
    details_verified: bool = False
    buyer_fee_rate: float | None = None
    buyer_fee_fixed: float | None = None


class CatawikiParseError(ValueError):
    """The response could not be identified as a Catawiki lot/result page."""


def canonical_lot_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname not in {"www.catawiki.com", "catawiki.com"}:
        raise CatawikiParseError("Kein Catawiki-Loslink")
    match = re.search(r"/l/(\d+)(?:[-/]|$)", parts.path)
    if not match:
        raise CatawikiParseError("Catawiki-Losnummer fehlt")
    return f"{CATAWIKI_BASE}/de/l/{match.group(1)}"


def _parse_money(text: str | None) -> float | None:
    if not text:
        return None
    normalized = text.replace("\xa0", " ").replace("EUR", "").replace("€", "").replace("â‚¬", "").strip()
    match = re.search(r"(\d[\d.,]*)", normalized)
    if not match:
        return None
    value = match.group(1)
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(",", "" if re.fullmatch(r"\d{1,3}(,\d{3})+", value) else ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", value):
        value = value.replace(".", "")
    try:
        return float(value)
    except ValueError:
        return None


def _extract_set_numbers(text: str) -> list[str]:
    numbers = []
    for match in re.finditer(r"\b(\d{4,6})(?:-1)?\b", text or ""):
        number = match.group(1)
        if 1900 <= int(number) <= 2099:
            continue
        if re.match(r"\s*(?:Teile|pieces|pcs|EUR|€)\b", text[match.end():], re.IGNORECASE):
            continue
        numbers.append(number)
    return list(dict.fromkeys(numbers))


def _extract_current_bid(text: str | None) -> float | None:
    if not text:
        return None

    for label in (r"Aktuelles Gebot|Current bid", r"Startgebot|Starting bid"):
        if not re.search(label, text, re.IGNORECASE):
            continue
        for amount in (r"(?:EUR|€)\s*(\d[\d.,]*)", r"(\d[\d.,]*)\s*(?:EUR|€)"):
            match = re.search(rf"(?:{label})\s*[:\-]?\s*{amount}", text, re.IGNORECASE)
            if match:
                return _parse_money(match.group(1))
        return None

    return None


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
        try:
            full_url = canonical_lot_url(urljoin(source_url, href))
        except CatawikiParseError:
            continue
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

    # Walk objects, never regex across serialized neighbouring lots.
    def walk(node):
        if isinstance(node, dict):
            title = node.get("title")
            lot_id = str(node.get("id", ""))
            if isinstance(title, str) and "lego" in title.lower() and lot_id.isdigit():
                full_url = f"{CATAWIKI_BASE}/de/l/{lot_id}"
                if full_url not in seen_urls:
                    candidates.append(CatawikiLotCandidate(
                        lot_id=lot_id, title=title, url=full_url, set_numbers=_extract_set_numbers(title),
                    ))
                    seen_urls.add(full_url)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return candidates


def lot_review_reasons(lot: CatawikiLotCandidate) -> list[str]:
    from app.domain.identity import is_set_offer

    reasons = []
    if lot.is_closed:
        reasons.append("Auktion beendet.")
    if not lot.details_verified:
        reasons.append("Losdetails konnten nicht gelesen werden.")
    if not lot.set_numbers or len(lot.set_numbers) != 1:
        reasons.append("Set-Zuordnung unklar oder mehrere Sets: Posten einzeln pruefen.")
    elif not is_set_offer(lot.title, lot.set_numbers[0]):
        reasons.append("Zubehoer oder Sammelangebot: keine Einzelset-Bewertung.")
    if lot.current_bid is None:
        reasons.append("Aktuelles EUR-Gebot nicht lesbar.")
    if lot.shipping_eur is None:
        reasons.append("Versand nach Deutschland fehlt: Kosten am Los pruefen.")
    if lot.condition != "NEW_SEALED":
        reasons.append("Nicht als neu und versiegelt bestaetigt: Zustand manuell pruefen.")
    return reasons


def parse_lot_page(html: str, url: str) -> CatawikiLotCandidate:
    """Extract details from a specific Catawiki lot page."""
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("h1")
    title = title_el.get_text(" ", strip=True) if title_el else ""
    if not title:
        raise CatawikiParseError("Catawiki-Losseite nicht lesbar (Titel fehlt)")
    content = soup.select_one("main") or soup
    for element in content.select("script, style, nav, footer, aside"):
        element.decompose()
    body_text = content.get_text(" ", strip=True)
    url = canonical_lot_url(url)
    lot_match = re.search(r"/l/(\d+)", url)

    shipping = None
    shipping_match = re.search(r"(\d[\d.,]*)\s*(?:EUR|€|â‚¬)\s+aus:", body_text, re.IGNORECASE)
    if shipping_match:
        shipping = _parse_money(shipping_match.group(1))

    current_bid = _extract_current_bid(body_text)
    condition, box_damage = classify_listing_condition(None, body_text)
    closed = bool(re.search(r"\b(?:Auktion beendet|Bieten beendet|Verkauft|Auction closed|Bidding closed)\b",
                            body_text, re.IGNORECASE))
    fee_match = re.search(
        r"(?:Käuferschutzgebühr|Kaeuferschutzgebuehr|Buyer Protection fee)\s*:?\s*"
        r"(\d+(?:[.,]\d+)?)\s*%\s*\+\s*(?:€\s*)?(\d+(?:[.,]\d+)?)",
        body_text, re.IGNORECASE,
    )

    return CatawikiLotCandidate(
        lot_id=lot_match.group(1) if lot_match else "",
        title=title,
        url=url,
        current_bid=current_bid,
        shipping_eur=shipping,
        set_numbers=_extract_set_numbers(title),
        condition=condition,
        box_damage=box_damage,
        is_closed=closed,
        details_verified=True,
        buyer_fee_rate=_parse_money(fee_match.group(1)) / 100 if fee_match else None,
        buyer_fee_fixed=_parse_money(fee_match.group(2)) if fee_match else None,
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
        lots = parse_category_page(html, category_url)[:limit]
        if not lots and not re.search(r"Keine (?:Ergebnisse|Lose)|No (?:results|lots)", html, re.IGNORECASE):
            raise CatawikiParseError("Keine Catawiki-Lose lesbar: Seite oder Zugriff pruefen")
        return lots

    async def get_lot(self, lot_url: str) -> CatawikiLotCandidate:
        html = await self._fetch(lot_url)
        return parse_lot_page(html, lot_url)
