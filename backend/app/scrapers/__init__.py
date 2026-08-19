"""Scraper modules for all data sources."""

from app.scrapers.amazon import AmazonScraper
from app.scrapers.base import BaseScraper, ScrapedOffer, ScrapedPrice, ScrapedSetInfo
from app.scrapers.brickeconomy import BrickEconomyScraper
from app.scrapers.brickmerge import BrickMergeScraper
from app.scrapers.ebay_sold import EbaySoldScraper
from app.scrapers.idealo import IdealoScraper
from app.scrapers.kleinanzeigen import KleinanzeigenScraper
from app.scrapers.lego_com import LegoComScraper

# All available scrapers
ALL_SCRAPERS: list[type[BaseScraper]] = [
    BrickMergeScraper,
    BrickEconomyScraper,
    EbaySoldScraper,
    KleinanzeigenScraper,
    AmazonScraper,
    IdealoScraper,
    LegoComScraper,
]

# Scrapers that provide market prices (for consensus calculation).
# IdealoScraper is deliberately absent: the site answers 403 from the
# production host, and its whole-page fallback then reported phantom prices
# (6,99 EUR for an 850 EUR set). Re-add once it can be fetched reliably.
PRICE_SCRAPERS: list[type[BaseScraper]] = [
    EbaySoldScraper,
    BrickEconomyScraper,
    BrickMergeScraper,
]

# Scrapers that provide active offers (for deal discovery)
OFFER_SCRAPERS: list[type[BaseScraper]] = [
    EbaySoldScraper,
    KleinanzeigenScraper,
    AmazonScraper,
    BrickMergeScraper,
]

# Scrapers that provide authoritative set metadata like UVP/EOL/theme.
METADATA_SCRAPERS: list[type[BaseScraper]] = [
    LegoComScraper,
    BrickMergeScraper,
    BrickEconomyScraper,
]

__all__ = [
    "BaseScraper",
    "ScrapedPrice",
    "ScrapedOffer",
    "ScrapedSetInfo",
    "BrickMergeScraper",
    "BrickEconomyScraper",
    "EbaySoldScraper",
    "KleinanzeigenScraper",
    "AmazonScraper",
    "IdealoScraper",
    "LegoComScraper",
    "ALL_SCRAPERS",
    "PRICE_SCRAPERS",
    "OFFER_SCRAPERS",
    "METADATA_SCRAPERS",
]
