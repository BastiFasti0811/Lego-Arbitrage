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

# Scrapers that provide market prices (for consensus calculation)
PRICE_SCRAPERS: list[type[BaseScraper]] = [
    EbaySoldScraper,
    BrickEconomyScraper,
    IdealoScraper,
    BrickMergeScraper,
]

# Scrapers that provide active offers (for deal discovery)
OFFER_SCRAPERS: list[type[BaseScraper]] = [
    EbaySoldScraper,
    KleinanzeigenScraper,
    AmazonScraper,
    BrickMergeScraper,
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
]
