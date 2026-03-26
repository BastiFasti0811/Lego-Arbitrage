"""Scheduled scraping tasks that refresh prices and active offers."""

import asyncio
from datetime import datetime

import structlog
from sqlalchemy import select

from app.engine.market_consensus import calculate_consensus
from app.models import LegoSet, Offer, PriceRecord, WatchlistItem
from app.models.base import async_session
from app.scrapers import METADATA_SCRAPERS, OFFER_SCRAPERS, PRICE_SCRAPERS
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


def _run_async(coro):
    """Run async code inside sync Celery tasks."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _apply_set_info(lego_set: LegoSet, info, *, overwrite_uvp: bool = False) -> bool:
    """Merge scraped metadata into a set record."""
    changed = False

    if info.set_name and (
        not lego_set.set_name
        or lego_set.set_name == lego_set.set_number
        or lego_set.set_name == f"LEGO {lego_set.set_number}"
    ):
        lego_set.set_name = info.set_name
        changed = True

    if info.theme and (not lego_set.theme or lego_set.theme == "Unknown"):
        lego_set.theme = info.theme
        changed = True

    if info.release_year and (not lego_set.release_year or lego_set.release_year == 2020):
        lego_set.release_year = info.release_year
        changed = True

    if info.uvp_eur and (overwrite_uvp or not lego_set.uvp_eur):
        lego_set.uvp_eur = info.uvp_eur
        changed = True

    if info.eol_status and info.eol_status != "UNKNOWN" and lego_set.eol_status != info.eol_status:
        lego_set.eol_status = info.eol_status
        changed = True

    if info.growth_percent is not None and lego_set.growth_percent != info.growth_percent:
        lego_set.growth_percent = info.growth_percent
        changed = True

    if info.image_url and not lego_set.image_url:
        lego_set.image_url = info.image_url
        changed = True

    if lego_set.release_year:
        category = lego_set.compute_category().value
        if lego_set.category != category:
            lego_set.category = category
            changed = True

    return changed


@celery_app.task(name="app.tasks.scrape_daily.scrape_set_prices")
def scrape_set_prices(set_number: str) -> dict:
    """Scrape all price sources for a single set."""
    return _run_async(_scrape_set_prices_async(set_number))


async def _scrape_set_prices_async(set_number: str) -> dict:
    """Async implementation of price scraping."""
    results = {"set_number": set_number, "prices": 0, "offers": 0, "errors": []}

    async with async_session() as session:
        result = await session.execute(select(LegoSet).where(LegoSet.set_number == set_number))
        lego_set = result.scalar_one_or_none()
        if not lego_set:
            results["errors"].append("Set not found in database")
            return results

        now = datetime.now(datetime.UTC)
        scraped_prices = []

        for scraper_cls in PRICE_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    price = await scraper.get_price(set_number)
                    if price:
                        scraped_prices.append(price)
                        session.add(
                            PriceRecord(
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
                        )
                        results["prices"] += 1
            except Exception as exc:
                results["errors"].append(f"{scraper_cls.__name__}: {exc}")
                logger.error("scrape.price_failed", scraper=scraper_cls.__name__, error=str(exc))

        for scraper_cls in METADATA_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    info = await scraper.get_set_info(set_number)
                    if info:
                        _apply_set_info(lego_set, info, overwrite_uvp=True)
            except Exception as exc:
                results["errors"].append(f"{scraper_cls.__name__} metadata: {exc}")
                logger.error("scrape.metadata_failed", scraper=scraper_cls.__name__, error=str(exc))

        for scraper_cls in OFFER_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    offers = await scraper.get_offers(set_number)
                    for offer in offers:
                        existing_offer_result = await session.execute(
                            select(Offer).where(
                                Offer.set_id == lego_set.id,
                                Offer.platform == offer.platform,
                                Offer.offer_url == offer.offer_url,
                            )
                        )
                        existing_offer = existing_offer_result.scalar_one_or_none()

                        if existing_offer:
                            existing_offer.offer_title = offer.offer_title
                            existing_offer.price_eur = offer.price_eur
                            existing_offer.shipping_eur = offer.shipping_eur
                            existing_offer.total_price_eur = offer.price_eur + (offer.shipping_eur or 0)
                            existing_offer.condition = offer.condition
                            existing_offer.box_damage = offer.box_damage
                            existing_offer.sealed = offer.sealed
                            existing_offer.seller_name = offer.seller_name
                            existing_offer.seller_rating = offer.seller_rating
                            existing_offer.seller_location = offer.seller_location
                            existing_offer.status = "ACTIVE"
                            existing_offer.last_seen_at = now
                            existing_offer.is_auction = offer.is_auction
                            existing_offer.auction_end = offer.auction_end
                        else:
                            session.add(
                                Offer(
                                    set_id=lego_set.id,
                                    platform=offer.platform,
                                    offer_url=offer.offer_url,
                                    offer_title=offer.offer_title,
                                    price_eur=offer.price_eur,
                                    shipping_eur=offer.shipping_eur,
                                    total_price_eur=offer.price_eur + (offer.shipping_eur or 0),
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
                            )

                        results["offers"] += 1
            except Exception as exc:
                results["errors"].append(f"{scraper_cls.__name__} offers: {exc}")
                logger.error("scrape.offers_failed", scraper=scraper_cls.__name__, error=str(exc))

        if scraped_prices:
            consensus = calculate_consensus(scraped_prices)
            if consensus.consensus_price > 0:
                lego_set.current_market_price = consensus.consensus_price
                lego_set.market_price_updated_at = now

        await session.commit()

    logger.info("scrape.complete", **results)
    return results


@celery_app.task(name="app.tasks.scrape_daily.scrape_all_watched_sets")
def scrape_all_watched_sets() -> dict:
    """Scrape all sets on the watchlist."""
    return _run_async(_scrape_all_watched_async())


async def _scrape_all_watched_async() -> dict:
    """Async implementation of full watchlist scraping."""
    summary = {"total_sets": 0, "success": 0, "errors": 0}

    async with async_session() as session:
        result = await session.execute(
            select(LegoSet.set_number)
            .join(WatchlistItem, WatchlistItem.set_id == LegoSet.id)
            .where(WatchlistItem.is_active)
        )
        set_numbers = [row[0] for row in result.all()]
        summary["total_sets"] = len(set_numbers)

    for set_number in set_numbers:
        try:
            result = await _scrape_set_prices_async(set_number)
            if result["errors"]:
                summary["errors"] += 1
            else:
                summary["success"] += 1
        except Exception as exc:
            summary["errors"] += 1
            logger.error("scrape.set_failed", set_number=set_number, error=str(exc))

    logger.info("scrape.all_complete", **summary)
    return summary


@celery_app.task(name="app.tasks.scrape_daily.refresh_known_set_metadata")
def refresh_known_set_metadata() -> dict:
    """Refresh UVP/EOL metadata for all known sets once per day."""
    return _run_async(_refresh_known_set_metadata_async())


async def _refresh_known_set_metadata_async() -> dict:
    summary = {"total_sets": 0, "updated": 0, "errors": 0}

    async with async_session() as session:
        result = await session.execute(select(LegoSet).order_by(LegoSet.updated_at.desc()))
        sets = result.scalars().all()
        summary["total_sets"] = len(sets)

        for lego_set in sets:
            try:
                changed = False
                for scraper_cls in METADATA_SCRAPERS:
                    try:
                        async with scraper_cls() as scraper:
                            info = await scraper.get_set_info(lego_set.set_number)
                            if info:
                                changed = _apply_set_info(lego_set, info, overwrite_uvp=True) or changed
                    except Exception as exc:
                        logger.error(
                            "scrape.metadata_refresh_failed",
                            set_number=lego_set.set_number,
                            scraper=scraper_cls.__name__,
                            error=str(exc),
                        )

                if changed:
                    summary["updated"] += 1
            except Exception as exc:
                summary["errors"] += 1
                logger.error("scrape.metadata_set_failed", set_number=lego_set.set_number, error=str(exc))

        await session.commit()

    logger.info("scrape.metadata_refresh_complete", **summary)
    return summary
