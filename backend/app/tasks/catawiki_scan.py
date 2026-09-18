"""Scheduled discovery scan for configured auction source categories."""


from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from billiard.exceptions import SoftTimeLimitExceeded

from app.api.routes.auctions import _discover_configured_platform
from app.notifications.telegram_bot import send_auction_discovery_summary
from app.runtime_settings import get_settings_map
from app.services.auction_scan_state import mark_notified, unnotified_results
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()

SUPPORTED_DISCOVERY_PLATFORMS = ("CATAWIKI", "WHATNOT", "BRICKLINK")


@celery_app.task(name="app.tasks.catawiki_scan.scan_configured_categories")
def scan_configured_categories() -> dict:
    return _run_async(_scan_configured_categories_async())


async def _scan_configured_categories_async() -> dict:
    summary = {"platforms": 0, "discovered": 0, "notified": 0, "skipped": [], "errors": []}

    for platform in SUPPORTED_DISCOVERY_PLATFORMS:
        try:
            config = await get_settings_map([f"{platform.lower()}_scan_urls", "catawiki_scan_frequency"])
            if not config.get(f"{platform.lower()}_scan_urls", ""):
                summary["skipped"].append(f"{platform}: keine URLs")
                continue
            if platform == "CATAWIKI" and not catawiki_scan_due(config.get("catawiki_scan_frequency")):
                summary["skipped"].append("CATAWIKI: heute nicht geplant")
                continue
            results = await _discover_configured_platform(platform, max_results_per_url=20)
            summary["platforms"] += 1
            summary["discovered"] += len(results)
            candidates = await unnotified_results(platform, [item.model_dump() for item in results])
            # Telegram renders five per message. Only mark lots actually included.
            for start in range(0, len(candidates), 5):
                batch = candidates[start:start + 5]
                if await send_auction_discovery_summary(batch):
                    await mark_notified(platform, [item["source_url"] for item in batch])
                    summary["notified"] += len(batch)
                else:
                    summary["errors"].append(f"{platform}: Benachrichtigung nicht gesendet")
                    break
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            logger.error("auction_scan.platform_failed", platform=platform, error=str(exc))
            summary["errors"].append(f"{platform}: {type(exc).__name__}")

    if summary["errors"]:
        raise RuntimeError(f"Auktionsscan fehlgeschlagen: {summary}")
    return summary


def catawiki_scan_due(frequency: str | None, now: datetime | None = None) -> bool:
    frequency = (frequency or "daily").strip().lower()
    if frequency not in {"daily", "weekly", "off"}:
        raise ValueError("Catawiki-Intervall muss daily, weekly oder off sein")
    now = (now or datetime.now(ZoneInfo("Europe/Berlin"))).astimezone(ZoneInfo("Europe/Berlin"))
    return frequency == "daily" or (frequency == "weekly" and now.weekday() == 6)
