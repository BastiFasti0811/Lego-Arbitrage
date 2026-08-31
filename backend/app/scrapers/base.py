"""Base scraper with retry logic, rate limiting, proxy support, and stealth mode."""

import asyncio
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import structlog
from fake_useragent import UserAgent
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings
from app.domain.offer_url import canonical_offer_url
from app.security.url_policy import validate_url_for_scraper

logger = structlog.get_logger()

# Minimum amount that can plausibly be a LEGO set price. Below this we are
# looking at shipping, per-piece figures or loyalty points — every scraper
# that grabbed "the first price on the page" got burned by exactly those.
MIN_PLAUSIBLE_SET_PRICE = 5.0


def parse_de_price(text: str) -> float | None:
    """Parse a German-format price ('1.234,56 €', '129,99 €', '850 €').

    The lookbehind prevents matches from starting mid-number, so '1499,99'
    never parses as 499.99.
    """
    match = re.search(r"(?<![\d.])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{2}))?\s*€", text)
    if not match:
        return None
    return float(f"{match.group(1).replace('.', '')}.{match.group(2) or '00'}")
ua = UserAgent()


@dataclass
class ScrapedPrice:
    """Standardized price data from any scraper."""

    source: str
    price_eur: float
    condition: str = "NEW_SEALED"
    currency: str = "EUR"
    sold_count: int | None = None
    median_price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    source_url: str | None = None
    is_reliable: bool = True
    notes: str | None = None
    scraped_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ScrapedOffer:
    """Standardized offer data from any marketplace scraper."""

    platform: str
    offer_url: str
    offer_title: str
    price_eur: float
    shipping_eur: float | None = None
    condition: str = "UNKNOWN"
    box_damage: bool = False
    # Passend zum condition-Default: ein unbekannter Zustand ist nicht
    # versiegelt. Die Kombination "UNKNOWN, aber sealed" landete sonst so in
    # der Datenbank und widersprach sich selbst.
    sealed: bool = False
    seller_name: str | None = None
    seller_rating: float | None = None
    seller_location: str | None = None
    is_auction: bool = False
    auction_end: datetime | None = None
    # True only once the listing page itself was read. The result list can only
    # ever guess the condition, and a guess must not overwrite a reading.
    details_verified: bool = False
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        # Search-result hrefs carry per-request tracking tokens. Stripping them
        # here keeps every scraper on the same stable identity, so the offer
        # upsert recognises a listing it has already seen.
        self.offer_url = canonical_offer_url(self.offer_url)


@dataclass
class OfferDetails:
    """What a listing's own page says, beyond what the result list showed.

    Fetched only for offers that survived the identity filter — the detail page
    costs one request per listing at a host that rate-limits.
    """

    condition: str
    box_damage: bool
    description: str | None = None


@dataclass
class ScrapedSetInfo:
    """Standardized set metadata from scrapers."""

    set_number: str
    set_name: str | None = None
    theme: str | None = None
    subtheme: str | None = None
    release_year: int | None = None
    piece_count: int | None = None
    minifigure_count: int | None = None
    uvp_eur: float | None = None
    eol_status: str | None = None
    eol_date: str | None = None
    image_url: str | None = None
    growth_percent: float | None = None


class UndecodableResponseError(RuntimeError):
    """Der Antwortkoerper ist keine Textseite.

    httpx gibt die Rohbytes zurueck, wenn es die Content-Encoding nicht
    entpacken kann; `.text` liest sie dann als Zeichen. Das Ergebnis ist
    ueberwiegend nicht druckbar, und jeder Parser dahinter sieht eine leere
    Seite — genau der Ausfall, der bei BrickEconomy fuenf Monate niemandem
    auffiel. Lieber laut scheitern als still nichts finden.
    """


_UNDECODED_SAMPLE = 2000

# Kurze Marker, die jede echte Seite dieser Scraper im ersten Sample-Fenster
# traegt (Doctype/html/head/body stehen am Dokumentanfang). Fuer eine
# zufaellige Bytefolge ist die Kollisionschance mit dem kuerzesten Token
# ("<head", 5 Zeichen) auf 2000 Positionen rund 2000 * 256**-5 < 1e-8 —
# faktisch nie. Fehlt jedes Token, ist die Antwort keine Seite, unabhaengig
# vom Steuerzeichenanteil. Das ist das primaere Signal; der Anteil unten ist
# nur das Netz fuer den (astronomisch unwahrscheinlichen) Fall, dass eines
# der Tokens doch einmal zufaellig auftaucht.
_HTML_MARKERS = ("<!doctype", "<html", "<head", "<body")

# Vorherige Version dieses Kommentars behauptete, unentpackte Bytes seien zu
# "rund einem Drittel" Steuerzeichen — falsch gemessen. Der Code zaehlt nur
# ord < 32 ohne Tab/LF/CR, also 29 von 256 Bytewerten ≈ 11,3 %. Echtes HTML
# liegt nahe 0 %, aber der Abstand zur alten 10-%-Grenze war zu klein:
# gzip-komprimiertes HTML, real als latin-1 decodiert (der Fallback-Pfad bei
# einem Server, der eine Single-Byte-Kodierung deklariert), maß 7,4-8,65 %,
# ein reiner Zufallslauf 9,95 % — beide unter der alten Grenze durchgerutscht.
# 5 % liegt sicher unter diesen gemessenen Faellen und weit ueber dem Wert
# echter Seiten.
_UNDECODED_RATIO = 0.05


def looks_undecoded(body: str) -> bool:
    """Ob ein Antwortkoerper Binaerdaten statt Text ist."""
    if not body:
        return False
    sample = body[:_UNDECODED_SAMPLE]
    lowered = sample.lower()
    if not any(marker in lowered for marker in _HTML_MARKERS):
        return True
    odd = sum(
        1 for ch in sample
        if ch == "�" or (ord(ch) < 32 and ch not in "\t\r\n")
    )
    return odd / len(sample) > _UNDECODED_RATIO


class BaseScraper(ABC):
    """Abstract base for all scrapers.

    Features:
    - Random delays between requests (anti-detection)
    - Rotating user agents
    - Optional proxy support
    - Retry with exponential backoff
    - Structured logging
    """

    def __init__(self):
        self.name = self.__class__.__name__
        self._client: httpx.AsyncClient | None = None

    def _base_headers(self) -> dict[str, str]:
        """Header des gemeinsamen Clients.

        Ohne `Accept-Encoding`: httpx setzt den Header selbst, und zwar genau
        auf die Verfahren, fuer die ein Decoder installiert ist. Ein
        handgeschriebener Wert ist eine Behauptung ueber Faehigkeiten, die
        niemand gegen die Umgebung prueft.
        """
        return {
            "User-Agent": ua.random,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with proper headers."""
        if self._client is None or self._client.is_closed:
            headers = self._base_headers()
            proxy = settings.proxy_url if settings.proxy_url else None
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=settings.scraper_timeout,
                proxy=proxy,
                follow_redirects=True,
            )
        return self._client

    async def _delay(self) -> None:
        """Random delay between requests to avoid detection."""
        delay = random.uniform(settings.scraper_delay_min, settings.scraper_delay_max)
        await asyncio.sleep(delay)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(
            # Harte Bot-Blocks nie wiederholen — ein 403/429 soll den Druck
            # senken, nicht verdreifachen. Ein UndecodableResponseError ebenso
            # wenig: ein fehlender Decoder im Image loest sich nicht durch
            # einen zweiten Versuch, sondern verdreifacht nur die Last auf
            # der Quelle und verlangsamt jeden Testlauf, der das provoziert.
            lambda e: not (
                (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (403, 429))
                or isinstance(e, UndecodableResponseError)
            )
        ),
        reraise=True,
    )
    async def _fetch(self, url: str) -> str:
        """Fetch a URL with retry logic and rate limiting."""
        safe_url = validate_url_for_scraper(url, self.name)
        await self._delay()
        client = await self._get_client()

        # Rotate user agent on each request
        client.headers["User-Agent"] = ua.random

        logger.info("scraper.fetch", scraper=self.name, url=safe_url[:100])
        response = await client.get(safe_url)
        validate_url_for_scraper(str(response.url), self.name)
        response.raise_for_status()

        body = response.text
        if looks_undecoded(body):
            logger.error(
                "scraper.undecodable_response",
                scraper=self.name,
                url=safe_url[:100],
                encoding=response.headers.get("content-encoding"),
            )
            raise UndecodableResponseError(
                f"{self.name}: Antwort von {safe_url[:80]} ist nicht dekodierbar "
                f"(content-encoding={response.headers.get('content-encoding')})"
            )
        return body

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @abstractmethod
    async def get_set_info(self, set_number: str) -> ScrapedSetInfo | None:
        """Get set metadata. Override in subclass."""
        ...

    @abstractmethod
    async def get_price(self, set_number: str) -> ScrapedPrice | None:
        """Get current price data. Override in subclass."""
        ...

    async def get_offers(self, set_number: str) -> list[ScrapedOffer]:
        """Get active offers/listings. Override in marketplace scrapers."""
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
