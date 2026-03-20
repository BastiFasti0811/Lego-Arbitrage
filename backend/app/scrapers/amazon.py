"""Amazon.de scraper — marketplace prices, third-party sellers, error prices."""

import re
from datetime import datetime, timezone

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedOffer, ScrapedPrice, ScrapedSetInfo

logger = structlog.get_logger()

AMAZON_BASE = "https://www.amazon.de"


def _parse_amazon_price(text: str) -> float | None:
    """Parse Amazon German price format: 123,45 € or EUR 123,45."""
    # German format: 1.234,56
    match = re.search(r"([\d.]+,\d{2})", text)
    if match:
        return float(match.group(1).replace(".", "").replace(",", "."))
    return None


class AmazonScraper(BaseScraper):
    """Scrapes Amazon.de for LEGO prices.

    Amazon is interesting for:
    - Third-party marketplace sellers (sometimes good deals)
    - Pricing errors (rare but profitable)
    - Price tracking over time
    - Sets still in retail (new items)

    IMPORTANT: Amazon has aggressive bot detection.
    Always use delays, rotating user agents, and ideally proxies.
    """

    def _build_search_url(self, set_number: str) -> str:
        """Build Amazon search URL."""
        query = f"LEGO+{set_number}"
        return f"{AMAZON_BASE}/s?k={query}&i=toys"

    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """Get basic set info from Amazon listing."""
        try:
            html = await self._fetch(self._build_search_url(set_number))
            soup = BeautifulSoup(html, "lxml")

            # Find first relevant result
            result = soup.select_one("[data-component-type='s-search-result']")
            if not result:
                return None

            title_el = result.select_one("h2 a span, .a-text-normal")
            title = title_el.get_text(strip=True) if title_el else None

            return ScrapedSetInfo(
                set_number=set_number,
                set_name=title,
            )
        except Exception as e:
            logger.error("amazon.set_info_failed", set_number=set_number, error=str(e))
            return None

    async def get_price(self, set_number: str) -> ScrapedPrice | None:
        """Get lowest Amazon price (including marketplace sellers)."""
        try:
            html = await self._fetch(self._build_search_url(set_number))
            soup = BeautifulSoup(html, "lxml")

            prices = []
            results = soup.select("[data-component-type='s-search-result']")

            for result in results[:10]:
                # Check title contains our set number
                title_el = result.select_one("h2 a span, .a-text-normal")
                if title_el:
                    title = title_el.get_text(strip=True)
                    if set_number not in title:
                        continue

                # Extract price
                price_el = result.select_one(".a-price .a-offscreen, .a-price-whole")
                if not price_el:
                    continue

                price = _parse_amazon_price(price_el.get_text())
                if price and 5.0 < price < 10000.0:
                    prices.append(price)

            if not prices:
                logger.warning("amazon.no_prices", set_number=set_number)
                return None

            lowest = min(prices)
            return ScrapedPrice(
                source="AMAZON",
                price_eur=lowest,
                min_price=lowest,
                max_price=max(prices),
                source_url=self._build_search_url(set_number),
                notes=f"Lowest of {len(prices)} Amazon listings",
            )
        except Exception as e:
            logger.error("amazon.price_failed", set_number=set_number, error=str(e))
            return None

    async def get_offers(self, set_number: str) -> list[ScrapedOffer]:
        """Get active Amazon offers for a LEGO set."""
        offers = []
        try:
            html = await self._fetch(self._build_search_url(set_number))
            soup = BeautifulSoup(html, "lxml")

            results = soup.select("[data-component-type='s-search-result']")

            for result in results[:15]:
                try:
                    # Title check
                    title_el = result.select_one("h2 a span, .a-text-normal")
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    if set_number not in title:
                        continue

                    # Link
                    link_el = result.select_one("h2 a, a.a-link-normal[href*='/dp/']")
                    href = link_el.get("href", "") if link_el else ""
                    offer_url = href if href.startswith("http") else f"{AMAZON_BASE}{href}"

                    # Price
                    price_el = result.select_one(".a-price .a-offscreen")
                    if not price_el:
                        continue
                    price = _parse_amazon_price(price_el.get_text())
                    if not price or price < 5.0:
                        continue

                    # Shipping (Amazon often free with Prime)
                    shipping = 0.0  # Assume free shipping for Prime-eligible
                    ship_el = result.select_one(".a-row.a-size-base .a-color-secondary")
                    if ship_el:
                        ship_text = ship_el.get_text()
                        if "Lieferung" in ship_text:
                            ship_price = _parse_amazon_price(ship_text)
                            if ship_price:
                                shipping = ship_price

                    # Seller info
                    seller_name = "Amazon.de"
                    seller_el = result.select_one(".a-row.a-size-small .a-size-small")
                    if seller_el and "Amazon" not in seller_el.get_text():
                        seller_name = seller_el.get_text(strip=True)

                    # Rating
                    rating_el = result.select_one(".a-icon-star-small .a-icon-alt")
                    rating = None
                    if rating_el:
                        rating_match = re.search(r"([\d,]+)", rating_el.get_text())
                        if rating_match:
                            rating = float(rating_match.group(1).replace(",", ".")) * 20  # 5-star → 100%

                    # Check if Prime eligible (indicates Amazon as seller)
                    is_prime = bool(result.select_one("[class*=prime], .a-icon-prime"))

                    offers.append(ScrapedOffer(
                        platform="AMAZON",
                        offer_url=offer_url,
                        offer_title=title,
                        price_eur=price,
                        shipping_eur=shipping,
                        seller_name=seller_name,
                        seller_rating=rating,
                        condition="NEW_SEALED",
                        sealed=True,
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.error("amazon.offers_failed", set_number=set_number, error=str(e))

        return offers
