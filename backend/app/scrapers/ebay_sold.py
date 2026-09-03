"""eBay.de Sold Items scraper — actual German market prices from completed sales."""

import re
from urllib.parse import quote_plus

import httpx
import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import (
    BaseScraper,
    ScrapedOffer,
    ScrapedPrice,
    ScrapedSetInfo,
    UndecodableResponseError,
)

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


def _is_challenge_page(html: str) -> bool:
    """eBay bot wall (splashui challenge) instead of search results."""
    return "splashui" in html or "entschuldigen Sie die St" in html


def _extract_card_offers(soup: BeautifulSoup) -> list[dict]:
    """Parse the current eBay results layout (li.s-card, 2026)."""
    results = []
    for card in soup.select("li.s-card"):
        title_el = card.select_one(".s-card__title")
        price_el = card.select_one(".s-card__price")
        if not title_el or not price_el:
            continue
        price = _parse_ebay_price(price_el.get_text())
        if not price or not 5.0 < price < 10000.0:
            continue
        text = card.get_text(" ", strip=True)
        if re.search(r"\bAnzeige\b|SPONSORED", text):
            continue
        title = title_el.get_text(strip=True)
        # eBays Template-/Platzhalterkarten tragen keinen echten Artikel.
        if not title or "Shop on eBay" in title:
            continue

        link_el = card.select_one("a[href*='itm/']")
        # Tracking-Query abschneiden — stabiler Upsert-Key über Laufzeiten hinweg.
        href = (link_el.get("href", "") if link_el else "").split("?")[0]

        if re.search(r"\bNeu\b|Brandneu", text):
            condition = "NEW_SEALED"
        elif re.search(r"Gebraucht", text, re.I):
            condition = "USED_COMPLETE"
        else:
            condition = "UNKNOWN"

        shipping = None
        if re.search(r"[Kk]ostenlos\w*\s+(Versand|Lieferung)", text):
            shipping = 0.0
        else:
            ship_match = re.search(r"(?:EUR|€)?\s*[\d.,]+\s*(?:EUR|€)?\s*Versand", text)
            if ship_match:
                shipping = _parse_ebay_price(ship_match.group(0))

        results.append(
            {
                "title": title,
                "price": price,
                "url": href,
                "condition": condition,
                "is_auction": bool(re.search(r"Gebot", text)),
                "shipping": shipping,
            }
        )
    return results


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
            condition = "&LH_ItemCondition=1000"  # New only
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

        # Strategy 0: current layout (2026) — li.s-card cards
        prices = [card["price"] for card in _extract_card_offers(soup)]
        if prices:
            return prices

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
        """Get market price from eBay sold items, falling back to active listings.

        Strategy:
        1. Sold search narrow ("neu versiegelt"), then broad.
        2. If the sold search yields nothing — challenge wall in any flavor,
           signin redirect (403) or genuinely empty — use the median of
           active BIN listings instead of giving up silently.
        """
        prices: list[float] = []
        html = ""
        url = self._build_sold_url(set_number, broad=False)
        search_type = "new_sealed"
        try:
            html = await self._fetch(url)
            prices = self._extract_sold_prices(BeautifulSoup(html, "lxml"))

            if len(prices) < 3:
                logger.info("ebay_sold.broadening_search", set_number=set_number, narrow_count=len(prices))
                url = self._build_sold_url(set_number, broad=True)
                html = await self._fetch(url)
                prices = self._extract_sold_prices(BeautifulSoup(html, "lxml"))
                search_type = "all_conditions"
        except Exception as e:
            logger.warning("ebay_sold.sold_search_failed", set_number=set_number, error=str(e)[:160])

        if prices:
            if len(prices) < 3:
                logger.warning("ebay_sold.too_few_results", set_number=set_number, count=len(prices))
            median = _calculate_median(prices)
            return ScrapedPrice(
                source="EBAY_SOLD",
                price_eur=median,
                median_price=median,
                min_price=min(prices),
                max_price=max(prices),
                sold_count=len(prices),
                source_url=url,
                is_reliable=len(prices) >= 5,
                notes=f"Median from {len(prices)} sold items ({search_type}, outliers filtered)",
            )

        if html and _is_challenge_page(html):
            logger.warning("ebay_sold.blocked", set_number=set_number)
        else:
            logger.warning("ebay_sold.no_sold_results", set_number=set_number)

        try:
            return await self._price_from_active_listings(set_number)
        except (UndecodableResponseError, httpx.HTTPError):
            # Anders als der Sold-Suche-Fang oben: hier gibt es keinen
            # weiteren Fallback mehr. Ein Bot-Block oder Verbindungsabbruch
            # an der letzten Quelle ist "eBay ist tot", nicht "kein Preis
            # gefunden" - die beiden sahen vor diesem Fix in _collect_prices
            # identisch aus.
            raise
        except Exception as e:
            logger.error("ebay_sold.price_failed", set_number=set_number, error=str(e))
            return None

    async def _price_from_active_listings(self, set_number: str) -> ScrapedPrice | None:
        """Fallback price signal while the sold search is bot-walled.

        Median of current Buy-It-Now asking prices — weaker than sold data,
        therefore never marked reliable.
        """
        url = self._build_active_url(set_number)
        html = await self._fetch(url)
        soup = BeautifulSoup(html, "lxml")
        prices = [card["price"] for card in _extract_card_offers(soup)]
        if len(prices) < 3:
            return None
        median = _calculate_median(prices)
        return ScrapedPrice(
            source="EBAY_ACTIVE",
            price_eur=median,
            median_price=median,
            min_price=min(prices),
            max_price=max(prices),
            sold_count=len(prices),
            source_url=url,
            is_reliable=False,
            notes=f"Fallback: Median aus {len(prices)} aktiven BIN-Listungen (Sold-Suche blockiert)",
        )

    async def get_offers(self, set_number: str) -> list[ScrapedOffer]:
        """Get active eBay Buy It Now offers."""
        offers = []
        try:
            url = self._build_active_url(set_number)
            html = await self._fetch(url)
            soup = BeautifulSoup(html, "lxml")

            for card in _extract_card_offers(soup)[:20]:
                offers.append(
                    ScrapedOffer(
                        platform="EBAY",
                        offer_url=card["url"],
                        offer_title=card["title"],
                        price_eur=card["price"],
                        shipping_eur=card["shipping"],
                        condition=card["condition"],
                        is_auction=card["is_auction"],
                    )
                )
            if offers:
                return offers

            # Legacy layout fallback
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
                        sealed=True,
                        condition="NEW_SEALED",
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.error("ebay_sold.offers_failed", set_number=set_number, error=str(e))

        return offers

    # ------------------------------------------------------------------
    # Freie Suchbegriffe (GENERIC-Artikel, PR 2) — kein Set-Nummer-Query,
    # kein "neu versiegelt"-Zustandsfilter, kein narrow/broad-Zweitversuch.
    # Der Lego-Pfad oben bleibt unveraendert; das hier sind eigenstaendige
    # Methoden fuer beliebige Produktbezeichnungen.
    # ------------------------------------------------------------------

    def _build_query_sold_url(self, query: str) -> str:
        """Build eBay search URL for sold items matching a free-text query."""
        params = (
            f"_nkw={quote_plus(query)}"
            f"&LH_Complete=1&LH_Sold=1&LH_PrefLoc=1&_sop=13&rt=nc"
        )
        return f"{EBAY_BASE}/sch/i.html?{params}"

    def _build_query_active_url(self, query: str) -> str:
        """Build eBay search URL for active (Buy It Now) listings matching a free-text query."""
        params = f"_nkw={quote_plus(query)}&LH_PrefLoc=1&LH_BIN=1&_sop=15"
        return f"{EBAY_BASE}/sch/i.html?{params}"

    async def _query_active_fallback(self, query: str) -> ScrapedPrice | None:
        """Fallback-Preis aus aktiven BIN-Listungen fuer einen freien Suchbegriff.

        Analog zu `_price_from_active_listings`, aber ohne Set-Nummer im Query —
        fuer GENERIC-Artikel, bei denen die Sold-Suche nichts liefert oder
        blockiert ist. Wie dort: nie als reliable markiert.
        """
        url = self._build_query_active_url(query)
        html = await self._fetch(url)
        soup = BeautifulSoup(html, "lxml")
        prices = [card["price"] for card in _extract_card_offers(soup)]
        if len(prices) < 3:
            return None
        median = _calculate_median(prices)
        return ScrapedPrice(
            source="EBAY_ACTIVE",
            price_eur=median,
            median_price=median,
            min_price=min(prices),
            max_price=max(prices),
            sold_count=len(prices),
            source_url=url,
            is_reliable=False,
            notes=f"Fallback: Median aus {len(prices)} aktiven BIN-Listungen (Sold-Suche ohne Treffer/blockiert)",
        )

    async def get_price_for_query(self, query: str) -> ScrapedPrice | None:
        """Marktpreis fuer einen freien Suchbegriff (GENERIC-Artikel, PR 2).

        Strategy: eine Sold-Suche (ohne Zustandsfilter, kein narrow/broad
        Zweitversuch). Liefert die Sold-Suche gar nichts — Bot-Wall oder
        genuinely leer — Fallback auf aktive BIN-Listungen (1-2 Treffer
        zaehlen als Erfolg und werden mit einer Low-Count-Warnung als
        EBAY_SOLD zurueckgegeben, kein Fallback-Trigger).
        """
        prices: list[float] = []
        html = ""
        url = self._build_query_sold_url(query)
        try:
            html = await self._fetch(url)
            prices = self._extract_sold_prices(BeautifulSoup(html, "lxml"))
        except Exception as e:
            logger.warning("ebay_sold.query_sold_search_failed", query=query, error=str(e)[:160])

        if prices:
            if len(prices) < 3:
                logger.warning("ebay_sold.query_too_few_results", query=query, count=len(prices))
            median = _calculate_median(prices)
            return ScrapedPrice(
                source="EBAY_SOLD",
                price_eur=median,
                median_price=median,
                min_price=min(prices),
                max_price=max(prices),
                sold_count=len(prices),
                source_url=url,
                is_reliable=len(prices) >= 5,
                notes=f"Median from {len(prices)} sold items (query search, outliers filtered)",
            )

        if html and _is_challenge_page(html):
            logger.warning("ebay_sold.query_blocked", query=query)
        else:
            logger.warning("ebay_sold.query_no_sold_results", query=query)

        try:
            return await self._query_active_fallback(query)
        except (UndecodableResponseError, httpx.HTTPError):
            # Bot-Wall oder Verbindungsabbruch an der letzten Quelle heisst "eBay ist
            # tot", nicht "keine Verkaeufe gefunden" — der Aufrufer muss das
            # unterscheiden koennen (siehe get_price, Commit e500dfa).
            raise
        except Exception as e:
            logger.error("ebay_sold.query_price_failed", query=query, error=str(e))
            return None
