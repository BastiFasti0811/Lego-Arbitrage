"""Kleinanzeigen.de scraper — private offers, often cheaper than retail."""


import httpx
import structlog
from bs4 import BeautifulSoup

from app.domain.condition import classify_listing_condition
from app.scrapers.base import (
    BaseScraper,
    OfferDetails,
    ScrapedOffer,
    ScrapedPrice,
    ScrapedSetInfo,
    parse_de_price,
)

logger = structlog.get_logger()

BASE_URL = "https://www.kleinanzeigen.de"


# Shared German price parser — '850 €', '1.200 € VB', '129,99 €'.
_parse_ka_price = parse_de_price


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

    async def fetch_offer_details(self, offer_url: str) -> OfferDetails | None:
        """Read condition and description from a single listing's page.

        The result list carries neither, so the scraper used to guess the
        condition from title keywords — a listing titled "Lego Eisvogel 10331"
        gave no hint that it comes without box or instructions.

        A 403/429 is re-raised: that is the host asking us to stop, and the
        caller reduces pressure instead of working through the remaining
        listings. Any other failure just leaves the offer unenriched.
        """
        try:
            html = await self._fetch(offer_url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (403, 429):
                raise
            logger.warning("kleinanzeigen.details_failed", url=offer_url, status=exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("kleinanzeigen.details_failed", url=offer_url, error=str(exc))
            return None

        soup = BeautifulSoup(html, "lxml")

        label = None
        details = soup.select(".addetailslist--detail")
        for detail in details:
            if detail.get_text(" ", strip=True).lower().startswith("zustand"):
                value_el = detail.select_one(".addetailslist--detail--value")
                label = value_el.get_text(strip=True) if value_el else None
                break

        description_el = soup.select_one("#viewad-description-text")
        description = description_el.get_text("\n", strip=True) if description_el else None

        if not details and description_el is None:
            # Weder Detailliste noch Beschreibung: das ist keine Anzeige ohne
            # Zustandsangabe, sondern eine Seite, die wir nicht gelesen haben
            # (Selektor-Drift, Captcha, Login-Wall). Stumm "UNKNOWN" zu melden
            # liesse den Cap-Log "10 Angebote geprueft" behaupten.
            logger.warning("kleinanzeigen.detail_page_unreadable", url=offer_url)
            return None

        title_el = soup.select_one("#viewad-title")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        # Manche Anzeigen fuehren "ohne OVP" nur im Titel.
        condition, box_damage = classify_listing_condition(label, f"{title}\n{description or ''}")
        return OfferDetails(condition=condition, box_damage=box_damage, description=description)

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

                    # Link
                    link_el = item.select_one("a[href*='/s-anzeige/'], a[href*='/anzeige/']")
                    if not link_el:
                        link_el = title_el if title_el.name == "a" else title_el.find_parent("a")
                    href = link_el.get("href", "") if link_el else ""
                    # Leerer Link ergäbe die Host-Konstante als Upsert-Key — lieber
                    # leer lassen, der Upsert überspringt Offers ohne URL.
                    offer_url = href if href.startswith("http") else (f"{BASE_URL}{href}" if href else "")

                    # Location
                    location_el = item.select_one(
                        "[class*=location], [class*=aditem-main--top]"
                    )
                    location = location_el.get_text(strip=True) if location_el else None

                    # Der Titel darf nur zaehlen, wenn er den Zustand
                    # ausspricht. Die alte Substring-Suche machte aus "neuwertig"
                    # und "Neupreis" ein NEW_SEALED — womit ein ungeprueftes
                    # Angebot besser dastand als ein geprueftes.
                    condition, box_damage = classify_listing_condition(None, title)
                    sealed = condition == "NEW_SEALED"

                    offers.append(ScrapedOffer(
                        platform="KLEINANZEIGEN",
                        offer_url=offer_url,
                        offer_title=title,
                        price_eur=price,
                        seller_location=location,
                        sealed=sealed,
                        box_damage=box_damage,
                        condition=condition,
                    ))
                except Exception:
                    continue

        except httpx.HTTPStatusError as e:
            # 403/429 muss nach oben — die 2h-Lane bricht darauf ab, statt den
            # blockenden Host über alle Watchlist-Sets weiter zu hämmern.
            if e.response.status_code in (403, 429):
                raise
            logger.error("kleinanzeigen.offers_failed", set_number=set_number, error=str(e))
        except Exception as e:
            logger.error("kleinanzeigen.offers_failed", set_number=set_number, error=str(e))

        return offers
