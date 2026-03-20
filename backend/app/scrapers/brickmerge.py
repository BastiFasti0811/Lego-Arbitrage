"""BrickMerge.de scraper — German retail prices, shop availability, discounts vs UVP."""

import re

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedOffer, ScrapedPrice, ScrapedSetInfo

logger = structlog.get_logger()

BASE_URL = "https://www.brickmerge.de"


class BrickMergeScraper(BaseScraper):
    """Scrapes BrickMerge.de for German retail LEGO prices.

    BrickMerge aggregates prices from German shops like Amazon, Alternate,
    JB Spielwaren, etc. Good for finding retail deals and UVP comparison.
    """

    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """Get set info from BrickMerge."""
        try:
            html = await self._fetch(f"{BASE_URL}/?sn={set_number}")
            soup = BeautifulSoup(html, "lxml")

            # Try to find the set name from the page title or heading
            title_el = soup.select_one("h1, .set-title, title")
            set_name = None
            if title_el:
                text = title_el.get_text(strip=True)
                # Remove "LEGO" prefix and set number for clean name
                set_name = re.sub(rf"(?i)lego\s*{set_number}\s*[-–:]\s*", "", text).strip()
                if not set_name or set_name == text:
                    set_name = text

            # UVP extraction
            uvp = None
            uvp_el = soup.find(string=re.compile(r"UVP|unverbindliche", re.I))
            if uvp_el:
                parent = uvp_el.parent if uvp_el.parent else None
                if parent:
                    uvp_match = re.search(r"(\d+[.,]\d{2})\s*€", parent.get_text())
                    if uvp_match:
                        uvp = float(uvp_match.group(1).replace(",", "."))

            # Theme extraction
            theme = None
            theme_el = soup.select_one(".theme, .set-theme, [class*=theme]")
            if theme_el:
                theme = theme_el.get_text(strip=True)

            return ScrapedSetInfo(
                set_number=set_number,
                set_name=set_name,
                theme=theme,
                uvp_eur=uvp,
            )
        except Exception as e:
            logger.error("brickmerge.set_info_failed", set_number=set_number, error=str(e))
            return None

    async def get_price(self, set_number: str) -> ScrapedPrice | None:
        """Get lowest current retail price from BrickMerge."""
        try:
            html = await self._fetch(f"{BASE_URL}/?sn={set_number}")
            soup = BeautifulSoup(html, "lxml")

            # Find price elements — BrickMerge shows shop prices
            prices = []
            # Look for price patterns in the page
            price_elements = soup.find_all(string=re.compile(r"\d+[.,]\d{2}\s*€"))
            for el in price_elements:
                match = re.search(r"(\d+[.,]\d{2})\s*€", el)
                if match:
                    price = float(match.group(1).replace(",", "."))
                    if 5.0 < price < 5000.0:  # Sanity check
                        prices.append(price)

            if not prices:
                logger.warning("brickmerge.no_prices", set_number=set_number)
                return None

            lowest = min(prices)

            return ScrapedPrice(
                source="BRICKMERGE",
                price_eur=lowest,
                source_url=f"{BASE_URL}/?sn={set_number}",
                notes=f"Lowest of {len(prices)} shop prices",
            )
        except Exception as e:
            logger.error("brickmerge.price_failed", set_number=set_number, error=str(e))
            return None

    async def get_offers(self, set_number: str) -> list[ScrapedOffer]:
        """Get all shop offers from BrickMerge for a set."""
        offers = []
        try:
            html = await self._fetch(f"{BASE_URL}/?sn={set_number}")
            soup = BeautifulSoup(html, "lxml")

            # Find shop offer rows/cards
            shop_rows = soup.select("tr[class*=shop], .shop-row, .offer-row, [class*=angebot]")
            if not shop_rows:
                # Fallback: try table rows with price data
                shop_rows = soup.select("table tr")

            for row in shop_rows:
                try:
                    # Extract shop name
                    shop_link = row.select_one("a")
                    if not shop_link:
                        continue

                    shop_name = shop_link.get_text(strip=True)
                    shop_url = shop_link.get("href", "")

                    # Extract price
                    price_text = row.get_text()
                    price_match = re.search(r"(\d+[.,]\d{2})\s*€", price_text)
                    if not price_match:
                        continue

                    price = float(price_match.group(1).replace(",", "."))
                    if price < 5.0 or price > 5000.0:
                        continue

                    offers.append(ScrapedOffer(
                        platform="BRICKMERGE",
                        offer_url=shop_url if shop_url.startswith("http") else f"{BASE_URL}{shop_url}",
                        offer_title=f"{shop_name} — LEGO {set_number}",
                        price_eur=price,
                        seller_name=shop_name,
                        condition="NEW_SEALED",
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.error("brickmerge.offers_failed", set_number=set_number, error=str(e))

        return offers
