"""Idealo.de scraper — German price comparison, shop availability."""

import re

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedPrice, ScrapedSetInfo

logger = structlog.get_logger()

BASE_URL = "https://www.idealo.de"


class IdealoScraper(BaseScraper):
    """Scrapes Idealo.de price comparison.

    Good for:
    - German retail price range
    - Shop availability
    - Price history trends
    """

    def _build_search_url(self, set_number: str) -> str:
        return f"{BASE_URL}/preisvergleich/MainSearchProductCategory.html?q=LEGO+{set_number}"

    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """Minimal set info from Idealo."""
        return ScrapedSetInfo(set_number=set_number)

    async def get_price(self, set_number: str) -> ScrapedPrice | None:
        """Get price range from Idealo."""
        try:
            html = await self._fetch(self._build_search_url(set_number))
            soup = BeautifulSoup(html, "lxml")

            prices = []
            # Idealo shows price prominently
            price_elements = soup.select(
                "[class*=productOffers-listItemOfferPrice], "
                "[class*=price], "
                "[data-testid*=price]"
            )

            for el in price_elements:
                text = el.get_text(strip=True)
                # German price: 123,45 € or ab 123,45 €
                match = re.search(r"(\d+(?:\.\d{3})*,\d{2})\s*€", text)
                if match:
                    price_str = match.group(1).replace(".", "").replace(",", ".")
                    price = float(price_str)
                    if 5.0 < price < 10000.0:
                        prices.append(price)

            if not prices:
                # Fallback: search for any price on the page
                all_prices = re.findall(r"(\d+(?:\.\d{3})*,\d{2})\s*€", soup.get_text())
                for p in all_prices:
                    price = float(p.replace(".", "").replace(",", "."))
                    if 5.0 < price < 10000.0:
                        prices.append(price)

            if not prices:
                logger.warning("idealo.no_prices", set_number=set_number)
                return None

            lowest = min(prices)
            return ScrapedPrice(
                source="IDEALO",
                price_eur=lowest,
                min_price=lowest,
                max_price=max(prices),
                source_url=self._build_search_url(set_number),
                notes=f"Lowest of {len(prices)} Idealo offers",
            )
        except Exception as e:
            logger.error("idealo.price_failed", set_number=set_number, error=str(e))
            return None
