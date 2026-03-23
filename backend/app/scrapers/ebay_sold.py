"""eBay.de Sold Items scraper — actual German market prices from completed sales."""

import re
from datetime import datetime, timezone

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedOffer, ScrapedPrice, ScrapedSetInfo

logger = structlog.get_logger()

EBAY_BASE = "https://www.ebay.de"


def _parse_ebay_price(text: str) -> float | None:
    """Parse a price string like 'EUR 1.234,56' or '1.234,56 €'."""
    # German format: 1.234,56
    match = re.search(r"([\d.]+,\d{2})", text)
    if match:
        price_str = match.group(1).replace(".", "").replace(",", ".")
        return float(price_str)
    # International format fallback: 1,234.56
    match = re.search(r"([\d,]+\.\d{2})", text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def _calculate_median(prices: list[float]) -> float:
    """Calculate median, filtering outliers (±30% from initial median)."""
    if not prices:
        return 0.0

    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    raw_median = sorted_prices[n // 2] if n % 2 else (sorted_prices[n // 2 - 1] + sorted_prices[n // 2]) / 2

    # Filter outliers: remove prices ±30% from median
    threshold = 0.30
    filtered = [p for p in sorted_prices if (1 - threshold) * raw_median <= p <= (1 + threshold) * raw_median]

    if not filtered:
        return raw_median

    n = len(filtered)
    return filtered[n // 2] if n % 2 else (filtered[n // 2 - 1] + filtered[n // 2]) / 2


class EbaySoldScraper(BaseScraper):
    """Scrapes eBay.de for sold/completed listings.

    This gives us ACTUAL market prices — what people really paid.
    Most reliable price source for German market.

    Strategy:
    - Search for "[set_number] LEGO neu versiegelt"
    - Filter: Verkaufte Artikel (LH_Complete=1&LH_Sold=1)
    - Last 60 days
    - Germany only
    - Calculate median, filter outliers
    """

    def _build_sold_url(self, set_number: str, broad: bool = False) -> str:
        """Build eBay search URL for sold items.

        Args:
            broad: If True, search without "neu versiegelt" filter
                   (useful for older sets with fewer sealed sales)
        """
        if broad:
            query = f"LEGO {set_number}"
            condition = ""  # All conditions
        else:
            query = f"LEGO {set_number} neu versiegelt"
            condition = f"&LH_ItemCondition=1000"  # New only
        params = (
            f"_nkw={query.replace(' ', '+')}"
            f"&LH_Complete=1"  # Completed
            f"&LH_Sold=1"  # Sold
            f"&LH_PrefLoc=1"  # Germany
            f"&_sop=13"  # Sort: newest first
            f"&rt=nc"
            f"{condition}"
        )
        return f"{EBAY_BASE}/sch/i.html?{params}"

    def _build_active_url(self, set_number: str) -> str:
        """Build eBay search URL for active listings (Buy It Now)."""
        query = f"LEGO {set_number}"
        params = (
            f"_nkw={query.replace(' ', '+')}"
            f"&LH_PrefLoc=1"  # Germany
            f"&LH_BIN=1"  # Buy It Now
            f"&_sop=15"  # Sort: price + shipping lowest
        )
        return f"{EBAY_BASE}/sch/i.html?{params}"

    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """eBay doesn't provide structured set info — return minimal."""
        return ScrapedSetInfo(set_number=set_number)

    def _extract_sold_prices(self, soup: BeautifulSoup) -> list[float]:
        """Extract prices from eBay search results.

        Supports both old (.s-item) and new (ul.srp-results > li) eBay layouts.
        """
        prices = []

        # Strategy 1: New eBay layout (2025+) — ul.srp-results > li
        ul = soup.select_one("ul.srp-results")
        if ul:
            items = ul.find_all("li", recursive=False)
            for item in items:
                # Skip sponsored/ad items
                if item.select_one("[class*=SPONSORED], [class*=promoted]"):
                    continue

                price_el = item.select_one(
                    "[class*=price], .BOLD, .s-card__attribute-row"
                )
                if not price_el:
                    continue

                price = _parse_ebay_price(price_el.get_text())
                if price and 5.0 < price < 10000.0:
                    prices.append(price)

        # Strategy 2: Old eBay layout (fallback)
        if not prices:
            items = soup.select(".s-item, .srp-results .s-item__wrapper")
            for item in items:
                if item.select_one(".s-item__ad-badge, [class*=SPONSORED]"):
                    continue
                price_el = item.select_one(".s-item__price, .POSITIVE")
                if not price_el:
                    continue
                price = _parse_ebay_price(price_el.get_text())
                if price and 5.0 < price < 10000.0:
                    prices.append(price)

        return prices

    async def get_price(self, set_number: str) -> ScrapedPrice | None:
        """Get market price from eBay sold items (median of last 60 days).

        Strategy:
        1. First try "LEGO {set_number} neu versiegelt" (new sealed)
        2. If too few results, retry with broader "LEGO {set_number}" search
        """
        try:
            # Attempt 1: Narrow search (new sealed)
            url = self._build_sold_url(set_number, broad=False)
            html = await self._fetch(url)
            soup = BeautifulSoup(html, "lxml")
            prices = self._extract_sold_prices(soup)
            search_type = "new_sealed"

            # Attempt 2: Broad search if too few results
            if len(prices) < 3:
                logger.info("ebay_sold.broadening_search", set_number=set_number, narrow_count=len(prices))
                url = self._build_sold_url(set_number, broad=True)
                html = await self._fetch(url)
                soup = BeautifulSoup(html, "lxml")
                prices = self._extract_sold_prices(soup)
                search_type = "all_conditions"

            if len(prices) < 3:
                logger.warning("ebay_sold.too_few_results", set_number=set_number, count=len(prices))
                if not prices:
                    return None

            median = _calculate_median(prices)

            return ScrapedPrice(
                source="EBAY_SOLD",
                price_eur=median,
                median_price=median,
                min_price=min(prices) if prices else None,
                max_price=max(prices) if prices else None,
                sold_count=len(prices),
                source_url=url,
                is_reliable=len(prices) >= 5,
                notes=f"Median from {len(prices)} sold items ({search_type}, outliers filtered)",
            )
        except Exception as e:
            logger.error("ebay_sold.price_failed", set_number=set_number, error=str(e))
            return None

    async def get_offers(self, set_number: str) -> list[ScrapedOffer]:
        """Get active eBay Buy It Now offers."""
        offers = []
        try:
            url = self._build_active_url(set_number)
            html = await self._fetch(url)
            soup = BeautifulSoup(html, "lxml")

            items = soup.select(".s-item, .srp-results .s-item__wrapper")

            for item in items[:20]:  # Max 20 offers
                try:
                    # Skip ads
                    if item.select_one(".s-item__ad-badge, [class*=SPONSORED]"):
                        continue

                    # Title
                    title_el = item.select_one(".s-item__title, .s-item__title--has-tags")
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    if "Shop on eBay" in title or not title:
                        continue

                    # Price
                    price_el = item.select_one(".s-item__price")
                    if not price_el:
                        continue
                    price = _parse_ebay_price(price_el.get_text())
                    if not price or price < 5.0:
                        continue

                    # Shipping
                    shipping = None
                    ship_el = item.select_one(".s-item__shipping, .s-item__freeXDays")
                    if ship_el:
                        ship_text = ship_el.get_text()
                        if "kostenlos" in ship_text.lower() or "gratis" in ship_text.lower():
                            shipping = 0.0
                        else:
                            shipping = _parse_ebay_price(ship_text)

                    # Link
                    link_el = item.select_one("a.s-item__link, a[href*='itm/']")
                    offer_url = link_el.get("href", "") if link_el else ""

                    # Seller
                    seller_el = item.select_one(".s-item__seller-info, .s-item__seller-info-text")
                    seller_name = seller_el.get_text(strip=True) if seller_el else None

                    # Auction check
                    is_auction = bool(item.select_one(".s-item__bidCount, [class*=bid]"))

                    offers.append(ScrapedOffer(
                        platform="EBAY",
                        offer_url=offer_url,
                        offer_title=title,
                        price_eur=price,
                        shipping_eur=shipping,
                        seller_name=seller_name,
                        is_auction=is_auction,
                        condition="NEW_SEALED",
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.error("ebay_sold.offers_failed", set_number=set_number, error=str(e))

        return offers
