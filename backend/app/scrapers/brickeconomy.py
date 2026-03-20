"""BrickEconomy.com scraper — global market prices, growth %, EOL status."""

import re
from datetime import datetime, timezone

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedPrice, ScrapedSetInfo

logger = structlog.get_logger()

BASE_URL = "https://www.brickeconomy.com"
USD_TO_EUR = 0.92  # Approximate conversion rate


class BrickEconomyScraper(BaseScraper):
    """Scrapes BrickEconomy for global LEGO market data.

    Primary source for:
    - Current market value (USD → EUR)
    - Growth % since release
    - EOL/retirement status & dates
    - Set metadata (piece count, minifigs, theme)
    """

    async def _search_set(self, set_number: str) -> str | None:
        """Find the set's page URL via search."""
        try:
            html = await self._fetch(f"{BASE_URL}/search?query={set_number}")
            soup = BeautifulSoup(html, "lxml")

            # Find first result link matching the set number
            links = soup.select("a[href*='/set/']")
            for link in links:
                href = link.get("href", "")
                if set_number in href or set_number in link.get_text():
                    return href if href.startswith("http") else f"{BASE_URL}{href}"

            return None
        except Exception as e:
            logger.error("brickeconomy.search_failed", set_number=set_number, error=str(e))
            return None

    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """Get comprehensive set info from BrickEconomy."""
        try:
            url = await self._search_set(set_number)
            if not url:
                logger.warning("brickeconomy.set_not_found", set_number=set_number)
                return None

            html = await self._fetch(url)
            soup = BeautifulSoup(html, "lxml")

            info = ScrapedSetInfo(set_number=set_number)

            # Set name
            h1 = soup.select_one("h1")
            if h1:
                name = h1.get_text(strip=True)
                # Clean: remove set number prefix
                name = re.sub(rf"^{set_number}\s*[-–:]\s*", "", name).strip()
                info.set_name = name

            page_text = soup.get_text()

            # Theme
            theme_match = re.search(r"Theme[:\s]+([A-Za-z\s&]+?)(?:\n|$|<)", page_text)
            if theme_match:
                info.theme = theme_match.group(1).strip()

            # Release year
            year_match = re.search(r"Year[:\s]+(\d{4})", page_text)
            if year_match:
                info.release_year = int(year_match.group(1))

            # Piece count
            pieces_match = re.search(r"Pieces?[:\s]+([\d,]+)", page_text)
            if pieces_match:
                info.piece_count = int(pieces_match.group(1).replace(",", ""))

            # Minifigures
            minifig_match = re.search(r"Minifig(?:ure)?s?[:\s]+(\d+)", page_text)
            if minifig_match:
                info.minifigure_count = int(minifig_match.group(1))

            # UVP / Retail price
            retail_match = re.search(r"(?:Retail|RRP|MSRP)[:\s]*\$?([\d,.]+)", page_text)
            if retail_match:
                usd_price = float(retail_match.group(1).replace(",", ""))
                info.uvp_eur = round(usd_price * USD_TO_EUR, 2)

            # EOL Status
            if re.search(r"Retired|Discontinued", page_text, re.I):
                info.eol_status = "RETIRED"
            elif re.search(r"Available|In Stock", page_text, re.I):
                info.eol_status = "AVAILABLE"

            # Growth %
            growth_match = re.search(r"Growth[:\s]+([-+]?\d+[.,]?\d*)%", page_text)
            if growth_match:
                info.growth_percent = float(growth_match.group(1).replace(",", "."))

            # Image
            img = soup.select_one("img[src*='lego'], img[src*='brick']")
            if img:
                info.image_url = img.get("src", "")

            return info
        except Exception as e:
            logger.error("brickeconomy.set_info_failed", set_number=set_number, error=str(e))
            return None

    async def get_price(self, set_number: str) -> ScrapedPrice | None:
        """Get current market value from BrickEconomy (USD → EUR)."""
        try:
            url = await self._search_set(set_number)
            if not url:
                return None

            html = await self._fetch(url)
            soup = BeautifulSoup(html, "lxml")
            page_text = soup.get_text()

            # Look for "New" value
            value_match = re.search(
                r"(?:Value|Current|Market)\s*(?:New|Sealed)?[:\s]*\$?([\d,.]+)",
                page_text, re.I
            )
            if not value_match:
                # Fallback: any USD price that looks like market value
                value_match = re.search(r"\$([\d,]+\.\d{2})", page_text)

            if not value_match:
                logger.warning("brickeconomy.no_price", set_number=set_number)
                return None

            usd_price = float(value_match.group(1).replace(",", ""))
            eur_price = round(usd_price * USD_TO_EUR, 2)

            # Growth info
            growth_match = re.search(r"Growth[:\s]+([-+]?\d+[.,]?\d*)%", page_text)
            notes = None
            if growth_match:
                notes = f"Growth: {growth_match.group(1)}%"

            return ScrapedPrice(
                source="BRICKECONOMY",
                price_eur=eur_price,
                price_original=usd_price,
                currency="USD",
                source_url=url,
                notes=notes,
            )
        except Exception as e:
            logger.error("brickeconomy.price_failed", set_number=set_number, error=str(e))
            return None
