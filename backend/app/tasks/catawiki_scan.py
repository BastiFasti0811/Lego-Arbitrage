"""Scheduled discovery scan for configured auction source categories."""

import asyncio

import structlog

from app.api.routes.auctions import _discover_configured_platform
from app.notifications.telegram_bot import send_auction_discovery_summary
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()

SUPPORTED_DISCOVERY_PLATFORMS = ("CATAWIKI", "WHATNOT", "BRICKLINK")


def _run_async(coro):
    return asyncio.run(coro)


@celery_app.task(name="app.tasks.catawiki_scan.scan_configured_categories")
def scan_configured_categories() -> dict:
    return _run_async(_scan_configured_categories_async())


async def _scan_configured_categories_async() -> dict:
    discovered = []
    categories_scanned = 0

    for platform in SUPPORTED_DISCOVERY_PLATFORMS:
        try:
            results = await _discover_configured_platform(platform, max_results_per_url=20)
            categories_scanned += 1
            discovered.extend(
                [
                    {
                        "source_platform": item.source_platform,
                        "set_number": item.set_number,
                        "current_bid": item.current_bid or 0.0,
                        "recommended_max_bid": item.recommended_max_bid or 0.0,
                    }
                    for item in results
                    if item.can_bid_now and item.recommended_max_bid is not None
                ]
            )
        except Exception as exc:
            logger.error("auction_scan.platform_failed", platform=platform, error=str(exc))

    notified = await send_auction_discovery_summary(discovered) if discovered else False
    return {"platforms": categories_scanned, "discovered": len(discovered), "notified": notified}
