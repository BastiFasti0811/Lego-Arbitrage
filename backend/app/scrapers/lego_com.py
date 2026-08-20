"""LEGO.com EOL checker — checks if sets are still available or retired."""

import re

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import MIN_PLAUSIBLE_SET_PRICE, BaseScraper, ScrapedPrice, ScrapedSetInfo

logger = structlog.get_logger()

BASE_URL = "https://www.lego.com/de-de"


def _extract_uvp(html: str) -> float | None:
    """Read the set's UVP from LEGO.com structured product data.

    Guessing from page text is not safe in either direction: the first
    price-like match wrote shipping costs into the database as UVP (0,40 EUR),
    and picking the largest instead would adopt cross-sell or gift-card
    amounts — a UVP that is too high blocks every correct price for that set
    via the plausibility guard, and nothing ever heals it. Structured markup
    or nothing; BrickMerge still supplies a UVP independently.
    """
    for pattern in (
        r'(?:product:price:amount|itemprop=["\']price["\'][^>]*content)["\']?\s*(?:content=)?["\'](\d+(?:\.\d{1,2})?)["\']',
        r'"price"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?',
    ):
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if value >= MIN_PLAUSIBLE_SET_PRICE:
                return value
    return None


class LegoComScraper(BaseScraper):
    """Checks LEGO.com/de-de for set availability and EOL status.

    Critical for investment decisions:
    - If still available → DO NOT invest!
    - If "Bald nicht mehr verfügbar" → Watch closely
    - If not found → Confirmed retired ✓
    """

    def _build_product_url(self, set_number: str) -> str:
        return f"{BASE_URL}/product/{set_number}"

    def _build_search_url(self, set_number: str) -> str:
        return f"{BASE_URL}/search?q={set_number}"

    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """Get set info and EOL status from LEGO.com."""
        try:
            # Try direct product page first
            html = await self._fetch(self._build_product_url(set_number))
            soup = BeautifulSoup(html, "lxml")
            page_text = soup.get_text()

            info = ScrapedSetInfo(set_number=set_number)

            # Check for 404 / not found → Retired
            if "nicht gefunden" in page_text.lower() or "page not found" in page_text.lower():
                info.eol_status = "RETIRED"
                return info

            # Set name from page title
            title_el = soup.select_one("h1, [data-test='product-overview-name']")
            if title_el:
                info.set_name = title_el.get_text(strip=True)

            # Check availability indicators
            if any(phrase in page_text.lower() for phrase in [
                "bald nicht mehr verfügbar",
                "last chance",
                "retiring soon",
                "letzte chance",
            ]):
                info.eol_status = "RETIRING_SOON"
            elif any(phrase in page_text.lower() for phrase in [
                "in den warenkorb",
                "add to bag",
                "jetzt kaufen",
                "verfügbar",
            ]):
                info.eol_status = "AVAILABLE"
            elif any(phrase in page_text.lower() for phrase in [
                "nicht verfügbar",
                "ausverkauft",
                "out of stock",
                "sold out",
            ]):
                # Could be temporarily out of stock or retired
                info.eol_status = "RETIRED"  # Conservative assumption
            else:
                info.eol_status = "UNKNOWN"

            # Price from LEGO.com
            info.uvp_eur = _extract_uvp(html)

            # Piece count
            pieces_match = re.search(r"(\d[\d.]*)\s*(?:Teile|pieces|pcs)", page_text, re.I)
            if pieces_match:
                info.piece_count = int(pieces_match.group(1).replace(".", ""))

            # Minifigures
            minifig_match = re.search(r"(\d+)\s*(?:Minifigure?n?|minifig)", page_text, re.I)
            if minifig_match:
                info.minifigure_count = int(minifig_match.group(1))

            # Age / Theme
            theme_el = soup.select_one("[class*=theme], [data-test*=theme]")
            if theme_el:
                info.theme = theme_el.get_text(strip=True)

            return info
        except Exception as e:
            # If page returns 404, the set is retired
            if "404" in str(e) or "Not Found" in str(e):
                return ScrapedSetInfo(set_number=set_number, eol_status="RETIRED")
            logger.error("lego_com.set_info_failed", set_number=set_number, error=str(e))
            return None

    async def get_price(self, set_number: str) -> ScrapedPrice | None:
        """Get official LEGO.com price (UVP)."""
        try:
            html = await self._fetch(self._build_product_url(set_number))
            soup = BeautifulSoup(html, "lxml")
            page_text = soup.get_text()

            price_match = re.search(r"(\d+[.,]\d{2})\s*€", page_text)
            if not price_match:
                return None

            price = float(price_match.group(1).replace(",", "."))
            if price < 1.0 or price > 10000.0:
                return None

            return ScrapedPrice(
                source="LEGO_COM",
                price_eur=price,
                source_url=self._build_product_url(set_number),
                notes="Official UVP from LEGO.com/de-de",
            )
        except Exception:
            return None
