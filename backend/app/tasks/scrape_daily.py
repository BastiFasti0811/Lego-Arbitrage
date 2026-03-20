"""Scheduled scraping tasks — runs via Celery Beat."""

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from app.models import LegoSet, WatchlistItem, PriceRecord, Offer
from app.models.base import async_session
from app.scrapers import ALL_SCRAPERS, OFFER_SCRAPERS, PRICE_SCRAPERS
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


def _run_async(coro):
    """Helper to run async code in sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.tasks.scrape_daily.scrape_set_prices")
def scrape_set_prices(set_number: str) -> dict:
    """Scrape all price sources for a single set."""
    return _run_async(_scrape_set_prices_async(set_number))


async def _scrape_set_prices_async(set_number: str) -> dict:
    """Async implementation of price scraping."""
    results = {"set_number": set_number, "prices": 0, "offers": 0, "errors": []}

    async with async_session() as session:
        # Find set in DB
        result = await session.execute(
            select(LegoSet).where(LegoSet.set_number == set_number)
        )
        lego_set = result.scalar_one_or_none()
        if not lego_set:
            results["errors"].append("Set not found in database")
            return results

        now = datetime.now(timezone.utc)

        # ── Scrape prices ────────────────────────────────
        for scraper_cls in PRICE_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    price = await scraper.get_price(set_number)
                    if price:
                        record = PriceRecord(
                            set_id=lego_set.id,
                            source=price.source,
                            price_eur=price.price_eur,
                            price_original=getattr(price, "price_original", None),
                            currency=price.currency,
                            condition=price.condition,
                            sold_count=price.sold_count,
                            median_price=price.median_price,
                            min_price=price.min_price,
                            max_price=price.max_price,
                            scraped_at=now,
                            source_url=price.source_url,
                            is_reliable=price.is_reliable,
                            notes=price.notes,
                        )
                        session.add(record)
                        results["prices"] += 1

                    # Also get set info to update metadata
                    info = await scraper.get_set_info(set_number)
                    if info:
                        if info.eol_status and info.eol_status != "UNKNOWN":
                            lego_set.eol_status = info.eol_status
                        if info.growth_percent:
                            lego_set.growth_percent = info.growth_percent

            except Exception as e:
                results["errors"].append(f"{scraper_cls.__name__}: {str(e)}")
                logger.error("scrape.price_failed", scraper=scraper_cls.__name__, error=str(e))

        # ── Scrape offers ────────────────────────────────
        for scraper_cls in OFFER_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    offers = await scraper.get_offers(set_number)
                    for offer in offers:
                        db_offer = Offer(
                            set_id=lego_set.id,
                            platform=offer.platform,
                            offer_url=offer.offer_url,
                            offer_title=offer.offer_title,
                            price_eur=offer.price_eur,
                            shipping_eur=offer.shipping_eur,
                            total_price_eur=(offer.price_eur + (offer.shipping_eur or 0)),
                            condition=offer.condition,
                            box_damage=offer.box_damage,
                            sealed=offer.sealed,
                            seller_name=offer.seller_name,
                            seller_rating=offer.seller_rating,
                            seller_location=offer.seller_location,
                            status="ACTIVE",
                            discovered_at=now,
                            last_seen_at=now,
                            is_auction=offer.is_auction,
                            auction_end=offer.auction_end,
                        )
                        session.add(db_offer)
                        results["offers"] += 1

            except Exception as e:
                results["errors"].append(f"{scraper_cls.__name__} offers: {str(e)}")
                logger.error("scrape.offers_failed", scraper=scraper_cls.__name__, error=str(e))

        # Update market price cache on set
        if results["prices"] > 0:
            lego_set.market_price_updated_at = now

        await session.commit()

    logger.info("scrape.complete", **results)
    return results


@celery_app.task(name="app.tasks.scrape_daily.scrape_all_watched_sets")
def scrape_all_watched_sets() -> dict:
    """Scrape all sets on the watchlist. Runs every 6 hours."""
    return _run_async(_scrape_all_watched_async())


async def _scrape_all_watched_async() -> dict:
    """Async implementation of full watchlist scrape."""
    summary = {"total_sets": 0, "success": 0, "errors": 0}

    async with async_session() as session:
        # Get all active watchlist set numbers
        result = await session.execute(
            select(LegoSet.set_number)
            .join(WatchlistItem, WatchlistItem.set_id == LegoSet.id)
            .where(WatchlistItem.is_active == True)
        )
        set_numbers = [row[0] for row in result.all()]
        summary["total_sets"] = len(set_numbers)

    # Scrape each set (sequentially to avoid rate limits)
    for set_number in set_numbers:
        try:
            result = await _scrape_set_prices_async(set_number)
            if result["errors"]:
                summary["errors"] += 1
            else:
                summary["success"] += 1
        except Exception as e:
            summary["errors"] += 1
            logger.error("scrape.set_failed", set_number=set_number, error=str(e))

    logger.info("scrape.all_complete", **summary)
    return summary
