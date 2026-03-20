"""Base scraper with retry logic, rate limiting, proxy support, and stealth mode."""

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import structlog
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = structlog.get_logger()
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
    scraped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    sealed: bool = True
    seller_name: str | None = None
    seller_rating: float | None = None
    seller_location: str | None = None
    is_auction: bool = False
    auction_end: datetime | None = None
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with proper headers."""
        if self._client is None or self._client.is_closed:
            headers = {
                "User-Agent": ua.random,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
            }
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
        reraise=True,
    )
    async def _fetch(self, url: str) -> str:
        """Fetch a URL with retry logic and rate limiting."""
        await self._delay()
        client = await self._get_client()

        # Rotate user agent on each request
        client.headers["User-Agent"] = ua.random

        logger.info("scraper.fetch", scraper=self.name, url=url[:100])
        response = await client.get(url)
        response.raise_for_status()
        return response.text

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
