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

    async def _fetch_detail_page(self, set_number: str) -> str:
        """Fetch BrickMerge detail page using ?find= redirect (avoids compression issues)."""
        import httpx
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Encoding": "identity",
        }
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15.0) as client:
            r = await client.get(f"{BASE_URL}/?find={set_number}")
            r.raise_for_status()
            return r.text

    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """Get set info from BrickMerge detail page."""
        try:
            html = await self._fetch_detail_page(set_number)
            soup = BeautifulSoup(html, "lxml")

            # Extract from title tag: "LEGO® Technic 42055 Schaufelradbagger (2016) ab 599,99 €..."
            title_el = soup.select_one("title")
            set_name = None
            theme = None
            release_year = None
            uvp = None

            if title_el:
                title_text = title_el.get_text(strip=True)
                # Parse: "LEGO® Technic 42055 Schaufelradbagger (2016) ab 599,99 €"
                name_match = re.search(
                    rf"LEGO®?\s+(\w[\w\s]*?)\s+{set_number}\s+(.+?)\s*\((\d{{4}})\)",
                    title_text,
                )
                if name_match:
                    theme = name_match.group(1).strip()
                    set_name = name_match.group(2).strip()
                    release_year = int(name_match.group(3))

            # H1 fallback: "LEGO® Technic 42055 Schaufelradbagger"
            if not set_name:
                h1 = soup.select_one("h1")
                if h1:
                    h1_text = h1.get_text(strip=True)
                    h1_match = re.search(
                        rf"LEGO®?\s*(\w[\w\s]*?)\s*{set_number}\s+(.+)",
                        h1_text,
                    )
                    if h1_match:
                        theme = theme or h1_match.group(1).strip()
                        set_name = h1_match.group(2).strip()

            # UVP extraction from page text
            uvp_match = re.search(r"UVP\s*[:.]?\s*(\d+[.,]\d{2})\s*€", html)
            if uvp_match:
                uvp = float(uvp_match.group(1).replace(",", "."))

            # EOL status
            eol_status = None
            if re.search(r"Auslaufartikel|EOL|End of Life", html, re.I):
                eol_status = "RETIRING_SOON"

            logger.info(
                "brickmerge.set_info",
                set_number=set_number,
                set_name=set_name,
                theme=theme,
                year=release_year,
            )

            return ScrapedSetInfo(
                set_number=set_number,
                set_name=set_name,
                theme=theme,
                release_year=release_year,
                uvp_eur=uvp,
                eol_status=eol_status,
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

    async def get_price_history(self, set_number: str) -> list[dict] | None:
        """Get historical price data from BrickMerge for trend analysis."""
        try:
            html = await self._fetch(f"{BASE_URL}/?sn={set_number}")
            soup = BeautifulSoup(html, "lxml")

            history = []
            scripts = soup.find_all("script")
            for script in scripts:
                text = script.string or ""
                date_matches = re.findall(r'"(\d{4}-\d{2}-\d{2})"', text)
                price_matches = re.findall(r'(\d+\.\d{2})', text)

                if date_matches and price_matches and len(date_matches) == len(price_matches):
                    for d, p in zip(date_matches, price_matches):
                        history.append({
                            "date": d,
                            "price": float(p),
                            "source": "BRICKMERGE",
                        })

            if not history:
                logger.debug("brickmerge.no_history", set_number=set_number)
                return None

            return sorted(history, key=lambda x: x["date"])
        except Exception as e:
            logger.error("brickmerge.history_failed", set_number=set_number, error=str(e))
            return None
