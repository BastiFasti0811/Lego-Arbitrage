"""Kleinanzeigen.de scraper — private offers, often cheaper than retail."""

import json
import re
from dataclasses import dataclass

import httpx
import structlog
from bs4 import BeautifulSoup, Tag

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

# Kleinanzeigen liefert je nach User-Agent zwei verschiedene Seiten: Desktop
# (Astro mit Tailwind-Klassen, 25 Treffer je Suche) und Mobil (BEM-Klassen
# "adlist--item", 8 Treffer, andere Treffermenge). Auch die Detailseite
# unterscheidet sich: mobil fehlt die Detailliste mit "Zustand". Der
# Zufalls-UA aus BaseScraper ist zu rund 70 % ein Mobilgeraet
# (fake-useragent 2.2, gemessen am 02.11.2026). Ein fester Desktop-Chrome
# macht beide Seiten vorhersagbar. Der Wert altert mit jedem Chrome-Release.
KLEINANZEIGEN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0.0.0 Safari/537.36"
)

# So viele Treffer je Suchseite werden hoechstens zu Angeboten.
MAX_OFFERS_PER_SEARCH = 20

# Shared German price parser — '850 €', '1.200 € VB', '129,99 €'.
_parse_ka_price = parse_de_price

# Das vollstaendige Preisfeld einer Trefferkarte: "18 €", "1.200 € VB",
# "129,99 €", "VB", "Zu verschenken". Verglichen wird der ganze Text eines
# Elements, damit "Versand für 6 €" aus der Beschreibung nie als Preis gilt.
_PRICE_FIELD_RE = re.compile(
    r"(?:(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{2})?\s*€(?:\s*VB)?|VB|Zu verschenken)",
    re.IGNORECASE,
)
# Eine Preissenkung zeigt den alten Preis durchgestrichen hinter dem neuen.
_STRUCK_TAGS = frozenset({"s", "del", "strike"})
_STRUCK_CLASS = "line-through"
_SKIPPED_TAGS = frozenset({"script", "style", "svg"})
# Ueberschrift oder Titel-Container: der Bild-Link davor traegt nur die Fotozahl.
_TITLE_LINK_SELECTOR = (
    "h1 a[href], h2 a[href], h3 a[href], h4 a[href], [class*=title] a[href], a[class*=title][href]"
)
_NO_RESULTS_RE = re.compile(r"keine Ergebnisse", re.IGNORECASE)


@dataclass(frozen=True)
class SearchHit:
    """Eine Trefferkarte der Suchseite, so wie sie dasteht."""

    title: str
    url: str
    price_text: str | None
    location: str | None


@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    cards: int  # Elemente mit data-adid, auch die ohne lesbaren Titel
    no_results: bool  # die Seite sagt ausdruecklich "keine Ergebnisse"


def _clean_text(element: Tag) -> str:
    # split() faellt auch auf geschuetzte Leerzeichen ("18\xa0€").
    return " ".join(element.get_text(" ", strip=True).split())


def _owning_card(element: Tag) -> Tag | None:
    """Die naechste Trefferkarte (data-adid) ueber dem Element, es selbst eingeschlossen."""
    node = element
    while isinstance(node, Tag):
        if node.has_attr("data-adid"):
            return node
        node = node.parent
    return None


def _own(card: Tag, selector: str) -> list[Tag]:
    """Elemente der Karte, die nicht zu einer eingeschachtelten Karte gehoeren.

    Die Mobilseite kuerzt Beschreibungen mitten im Tag ("<br ..."). Der Parser
    schliesst dann den Container nicht mehr, und alle folgenden Karten landen
    IN dieser Karte. Ohne die Pruefung bekaeme sie deren Preise und Titel.
    """
    return [element for element in card.select(selector) if _owning_card(element) is card]


def _is_struck(element: Tag, card: Tag) -> bool:
    node = element
    while isinstance(node, Tag):
        if node.name in _STRUCK_TAGS or _STRUCK_CLASS in (node.get("class") or []):
            return True
        if node is card:
            return False
        node = node.parent
    return False


def _title_link(card: Tag) -> Tag | None:
    for link in _own(card, _TITLE_LINK_SELECTOR):
        if _clean_text(link):
            return link
    return None


def _ld_title(card: Tag) -> str | None:
    """Rueckfall: das Bild-JSON-LD der Karte nennt den Anzeigentitel."""
    for script in _own(card, 'script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        if isinstance(data, dict):
            title = data.get("title") or data.get("name")
            if isinstance(title, str) and title.strip():
                return " ".join(title.split())
    return None


def _ad_url(card: Tag, title_link: Tag | None) -> str:
    candidates = [card.get("data-href"), title_link.get("href") if title_link else None]
    candidates += [link.get("href") for link in _own(card, "a[href*='/s-anzeige/']")]
    for href in candidates:
        if isinstance(href, str) and "/s-anzeige/" in href:
            href = href.strip()
            return href if href.startswith("http") else f"{BASE_URL}{href}"
    # Ohne Anzeigenlink gibt es keinen stabilen Upsert-Key; der Upsert
    # ueberspringt leere URLs.
    return ""


def _price_text(card: Tag) -> str | None:
    for element in card.find_all(True):
        if element.name in _SKIPPED_TAGS or _owning_card(element) is not card:
            continue
        text = _clean_text(element)
        if text and _PRICE_FIELD_RE.fullmatch(text) and not _is_struck(element, card):
            return text
    return None


def _location(card: Tag) -> str | None:
    for element in _own(card, "[class*=location]"):
        text = _clean_text(element)
        if text:
            return text
    # Desktop: Ort steht neben dem Icon <svg data-title="locationOutline">.
    for icon in _own(card, "svg[data-title]"):
        if "location" in (icon.get("data-title") or "").lower() and isinstance(icon.parent, Tag):
            text = _clean_text(icon.parent)
            if text:
                return text
    return None


def parse_search_page(html: str) -> SearchPage:
    """Trefferkarten einer Suchseite lesen, unabhaengig vom Layout.

    Anker sind Attribute, die beide Layouts tragen (data-adid, data-href) und
    die Bedeutung der Elemente (Ueberschrift, vollstaendiges Preisfeld), nicht
    Klassennamen: an "aditem" hing der alte Parser, und als Kleinanzeigen das
    Layout umbaute, meldete der Scan zwei Monate lang 0 Angebote bei 0 Fehlern.
    """
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("[data-adid]")
    hits = []
    for card in cards:
        link = _title_link(card)
        title = _clean_text(link) if link else _ld_title(card)
        if not title:
            continue
        hits.append(
            SearchHit(
                title=title,
                url=_ad_url(card, link),
                price_text=_price_text(card),
                location=_location(card),
            )
        )
    no_results = not cards and bool(_NO_RESULTS_RE.search(soup.get_text(" ", strip=True)))
    return SearchPage(hits=hits, cards=len(cards), no_results=no_results)


class KleinanzeigenScraper(BaseScraper):
    """Scrapes Kleinanzeigen.de (formerly eBay Kleinanzeigen).

    Private sellers often have lower prices, but:
    - No buyer protection
    - Shipping negotiable
    - Higher fraud risk → check seller profile
    - VB (Verhandlungsbasis) = negotiable price

    Plain httpx with a fixed desktop user agent. Search and detail pages came
    through from the prod host without captcha on 02.11.2026; a block shows up
    as 403/429 and is raised to the caller.
    """

    # BaseScraper._fetch nimmt sonst je Abruf einen Zufalls-UA (meist mobil).
    user_agent_override = KLEINANZEIGEN_USER_AGENT

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

    async def _search(self, set_number: str) -> SearchPage:
        html = await self._fetch(self._build_search_url(set_number))
        page = parse_search_page(html)
        if not page.hits and not page.no_results:
            # HTTP 200, aber nichts lesbar: Layout-Drift oder eine Seite, die
            # keine Trefferliste ist. Genau das lief zwei Monate ohne Logzeile.
            logger.warning(
                "kleinanzeigen.search_unreadable",
                set_number=set_number,
                cards=page.cards,
                length=len(html),
            )
        return page

    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """Kleinanzeigen doesn't have structured set data."""
        return ScrapedSetInfo(set_number=set_number)

    async def get_price(self, set_number: str) -> ScrapedPrice | None:
        """Get average asking price from Kleinanzeigen listings."""
        try:
            page = await self._search(set_number)

            prices = []
            for hit in page.hits:
                # Skip "Zu verschenken" (free) and "VB" only listings
                if not hit.price_text or "verschenken" in hit.price_text.lower():
                    continue
                price = _parse_ka_price(hit.price_text)
                if price and 5.0 < price < 10000.0 and (set_number in hit.title or "lego" in hit.title.lower()):
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
            page = await self._search(set_number)

            for hit in page.hits[:MAX_OFFERS_PER_SEARCH]:
                title = hit.title
                if set_number not in title and "lego" not in title.lower():
                    continue
                if not hit.price_text or "verschenken" in hit.price_text.lower():
                    continue
                price = _parse_ka_price(hit.price_text)
                if not price or price < 5.0:
                    continue

                # Der Titel darf nur zaehlen, wenn er den Zustand
                # ausspricht. Die alte Substring-Suche machte aus "neuwertig"
                # und "Neupreis" ein NEW_SEALED — womit ein ungeprueftes
                # Angebot besser dastand als ein geprueftes.
                condition, box_damage = classify_listing_condition(None, title)

                offers.append(ScrapedOffer(
                    platform="KLEINANZEIGEN",
                    offer_url=hit.url,
                    offer_title=title,
                    price_eur=price,
                    seller_location=hit.location,
                    sealed=condition == "NEW_SEALED",
                    box_damage=box_damage,
                    condition=condition,
                ))

        except httpx.HTTPStatusError as e:
            # 403/429 muss nach oben — die 2h-Lane bricht darauf ab, statt den
            # blockenden Host über alle Watchlist-Sets weiter zu hämmern.
            if e.response.status_code in (403, 429):
                raise
            logger.error("kleinanzeigen.offers_failed", set_number=set_number, error=str(e))
        except Exception as e:
            logger.error("kleinanzeigen.offers_failed", set_number=set_number, error=str(e))

        return offers
