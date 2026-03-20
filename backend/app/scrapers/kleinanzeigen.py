"""Kleinanzeigen.de scraper — private offers, often cheaper than retail."""

import re
from datetime import datetime, timezone

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedOffer, ScrapedPrice, ScrapedSetInfo

logger = structlog.get_logger()

BASE_URL = "https://www.kleinanzeigen.de"


def _parse_ka_price(text: str) -> float | None:
    """Parse Kleinanzeigen price: '850 €', '1.200 € VB'."""
    match = re.search(r"([\d.]+)\s*€", text.replace(",", "."))
    if match:
        return float(match.group(1).replace(".", ""))
    # Handle decimal: 850,00
    match = re.search(r"([\d.]+,\d{2})\s*€", text)
    if match:
        return float(match.group(1).replace(".", "").replace(",", "."))
    return None


class KleinanzeigenScraper(BaseScraper):
    """Scrapes Kleinanzeigen.de (formerly eBay Kleinanzeigen).

    Private sellers often have lower prices, but:
    - No buyer protection
    - Shipping negotiable
    - Higher fraud risk → check seller profile
    - VB (Verhandlungsbasis) = negotiable price

    IMPORTANT: Kleinanzeigen has Captcha and bot detection.
    Production use requires Playwright with stealth mode.
    """

    def _build_search_url(self, set_number: str) -> str:
        """Build Kleinanzeigen search URL."""
        query = f"LEGO+{set_number}"
        return f"{BASE_URL}/s-{query}/k0"

    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """Kleinanzeigen doesn't have structured set data."""
        return ScrapedSetInfo(set_number=set_number)

    async def get_price(self, set_number: str) -> ScrapedPrice | None:
        """Get average asking price from Kleinanzeigen listings."""
        try:
            html = await self._fetch(self._build_search_url(set_number))
            soup = BeautifulSoup(html, "lxml")

            prices = []
            items = soup.select(
                "[class*=aditem], "
                "[data-testid*=ad-listitem], "
                ".ad-listitem, "
                "article[class*=ad]"
            )

            for item in items:
                # Price
                price_el = item.select_one(
                    "[class*=price], "
                    "[data-testid*=price], "
                    ".aditem-main--middle--price, "
                    "p[class*=price]"
                )
                if not price_el:
                    continue

                price_text = price_el.get_text(strip=True)

                # Skip "Zu verschenken" (free) and "VB" only listings
                if "verschenken" in price_text.lower():
                    continue

                price = _parse_ka_price(price_text)
                if price and 5.0 < price < 10000.0:
                    # Check if title contains our set number
                    title_el = item.select_one(
                        "a[class*=title], "
                        "[class*=title], "
                        "h2, h3"
                    )
                    if title_el:
                        title = title_el.get_text(strip=True)
                        if set_number in title or "lego" in title.lower():
                            prices.append(price)

            if not prices:
                logger.warning("kleinanzeigen.no_prices", set_number=set_number)
                return None

            # Use median (private sellers are all over the place)
            sorted_p = sorted(prices)
            n = len(sorted_p)
            median = sorted_p[n // 2] if n % 2 else (sorted_p[n // 2 - 1] + sorted_p[n // 2]) / 2

            return ScrapedPrice(
                source="KLEINANZEIGEN",
                price_eur=median,
                median_price=median,
                min_price=min(prices),
                max_price=max(prices),
                sold_count=len(prices),
                source_url=self._build_search_url(set_number),
                is_reliable=len(prices) >= 3,
                notes=f"Median from {len(prices)} listings (asking prices, not sold)",
            )
        except Exception as e:
            logger.error("kleinanzeigen.price_failed", set_number=set_number, error=str(e))
            return None

    async def get_offers(self, set_number: str) -> list[ScrapedOffer]:
        """Get active Kleinanzeigen offers."""
        offers = []
        try:
            html = await self._fetch(self._build_search_url(set_number))
            soup = BeautifulSoup(html, "lxml")

            items = soup.select(
                "[class*=aditem], "
                "[data-testid*=ad-listitem], "
                ".ad-listitem, "
                "article[class*=ad]"
            )

            for item in items[:20]:
                try:
                    # Title
                    title_el = item.select_one(
                        "a[class*=title], [class*=title], h2, h3"
                    )
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    if set_number not in title and "lego" not in title.lower():
                        continue

                    # Price
                    price_el = item.select_one(
                        "[class*=price], [data-testid*=price], p[class*=price]"
                    )
                    if not price_el:
                        continue
                    price_text = price_el.get_text(strip=True)
                    if "verschenken" in price_text.lower():
                        continue
                    price = _parse_ka_price(price_text)
                    if not price or price < 5.0:
                        continue

                    is_negotiable = "VB" in price_text or "vb" in price_text

                    # Link
                    link_el = item.select_one("a[href*='/s-anzeige/'], a[href*='/anzeige/']")
                    if not link_el:
                        link_el = title_el if title_el.name == "a" else title_el.find_parent("a")
                    href = link_el.get("href", "") if link_el else ""
                    offer_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                    # Location
                    location_el = item.select_one(
                        "[class*=location], [class*=aditem-main--top]"
                    )
                    location = location_el.get_text(strip=True) if location_el else None

                    # Condition from title analysis
                    sealed = any(kw in title.lower() for kw in ["versiegelt", "sealed", "neu", "ovp", "misb"])
                    box_damage = any(kw in title.lower() for kw in ["beschädigt", "dellen", "damage"])

                    offers.append(ScrapedOffer(
                        platform="KLEINANZEIGEN",
                        offer_url=offer_url,
                        offer_title=title,
                        price_eur=price,
                        seller_location=location,
                        sealed=sealed,
                        box_damage=box_damage,
                        condition="NEW_SEALED" if sealed else "UNKNOWN",
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.error("kleinanzeigen.offers_failed", set_number=set_number, error=str(e))

        return offers
