"""Scheduled discovery scan for configured auction source categories."""


from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from billiard.exceptions import SoftTimeLimitExceeded

from app.api.routes.auctions import _discover_configured_platform, _evaluate_lot
from app.notifications.telegram_bot import send_auction_discovery_summary
from app.runtime_settings import get_settings_map
from app.services.auction_scan_state import mark_notified, save_scan, unnotified_results
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()

SUPPORTED_DISCOVERY_PLATFORMS = ("CATAWIKI", "WHATNOT", "BRICKLINK")
# Vom Server aus gesperrt (Akamai, IP-Sperre): diese Plattformen scannt der
# Heimrechner und liefert ueber evaluate_remote_scan ab.
HOME_RUNNER_PLATFORMS = frozenset({"CATAWIKI"})


@celery_app.task(name="app.tasks.catawiki_scan.scan_configured_categories")
def scan_configured_categories() -> dict:
    return _run_async(_scan_configured_categories_async())


async def _scan_configured_categories_async() -> dict:
    summary = {"platforms": 0, "discovered": 0, "notified": 0, "skipped": [], "errors": []}
    # Ohne Telegram gibt es nichts zu melden -- das ist Konfiguration, kein Fehler.
    telegram_configured = await _telegram_configured()

    for platform in SUPPORTED_DISCOVERY_PLATFORMS:
        if platform in HOME_RUNNER_PLATFORMS:
            summary["skipped"].append(f"{platform}: laeuft ueber den Heimrechner")
            continue
        try:
            config = await get_settings_map([f"{platform.lower()}_scan_urls", "catawiki_scan_frequency"])
            if not config.get(f"{platform.lower()}_scan_urls", ""):
                summary["skipped"].append(f"{platform}: keine URLs")
                continue
            if platform == "CATAWIKI" and not catawiki_scan_due(config.get("catawiki_scan_frequency")):
                summary["skipped"].append("CATAWIKI: heute nicht geplant")
                continue
            # Teilergebnis nach Zeitlimit oder Sperre trotzdem melden, der Fehler
            # zaehlt danach weiter und laesst den Task rot enden.
            results, scan_errors = await _discover_configured_platform(platform, max_results_per_url=20)
            summary["platforms"] += 1
            summary["discovered"] += len(results)
            summary["errors"].extend(scan_errors)
            await _notify_new_results(platform, [item.model_dump() for item in results], summary, telegram_configured)
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            logger.error("auction_scan.platform_failed", platform=platform, error=str(exc))
            summary["errors"].append(f"{platform}: {type(exc).__name__}")

    if summary["errors"]:
        raise RuntimeError(f"Auktionsscan fehlgeschlagen: {summary}")
    return summary


async def _notify_new_results(platform: str, results: list[dict], summary: dict, telegram_configured: bool) -> None:
    """Neue Freigaben per Telegram melden; nur tatsaechlich versendete Lose markieren."""
    candidates = await unnotified_results(platform, results)
    if candidates and not telegram_configured:
        summary["skipped"].append(f"{platform}: Telegram nicht konfiguriert, {len(candidates)} Treffer ungemeldet")
        return
    # Telegram renders five per message. Only mark lots actually included.
    for start in range(0, len(candidates), 5):
        batch = candidates[start:start + 5]
        if await send_auction_discovery_summary(batch):
            await mark_notified(platform, [item["source_url"] for item in batch])
            summary["notified"] += len(batch)
        else:
            summary["errors"].append(f"{platform}: Benachrichtigung nicht gesendet")
            break


async def _telegram_configured() -> bool:
    telegram = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    return bool(telegram.get("telegram_bot_token") and telegram.get("telegram_chat_id"))


@celery_app.task(
    name="app.tasks.catawiki_scan.evaluate_remote_scan",
    # Je Los mit Freigabe-Chance mehrere Marktabfragen; 50 solcher Lose passen.
    time_limit=1800,
    soft_time_limit=1700,
)
def evaluate_remote_scan(payload: dict) -> dict:
    return _run_async(_evaluate_remote_scan_async(payload))


async def _evaluate_remote_scan_async(payload: dict) -> dict:
    """Vom Heimrechner gelesene Catawiki-Lose bewerten, speichern und melden."""
    from app.services.remote_scan import RemoteScanResults

    data = RemoteScanResults.model_validate(payload)
    try:
        return await _evaluate_remote_lots(data)
    finally:
        # Auch nach Fehler oder Zeitlimit: sonst blockiert die Sperre bis JOB_LEASE.
        await _finish_remote_job(data.job_id)


async def _finish_remote_job(job_id: str) -> None:
    from app.models.base import async_session
    from app.services.remote_scan import finish_job

    try:
        async with async_session() as session:
            await finish_job(session, job_id)
    except Exception as exc:  # noqa: BLE001 -- die Sperre verfaellt dann nach JOB_LEASE
        logger.error("remote_scan.finish_failed", job_id=job_id, error=repr(exc)[:300])


async def _evaluate_remote_lots(data) -> dict:
    from app.services.remote_scan import PLATFORM
    summary = {"platforms": 1, "discovered": 0, "notified": 0, "skipped": [], "errors": list(data.errors)}
    evaluated = []
    for lot in data.lots:
        try:
            result = await _evaluate_lot(category_url=lot.category_url, platform=PLATFORM, lot=lot.to_candidate())
        except SoftTimeLimitExceeded:
            await save_scan(PLATFORM, [item.model_dump() for item in evaluated],
                            [*summary["errors"], "Zeitlimit bei der Bewertung erreicht"])
            raise
        except Exception as exc:  # noqa: BLE001 -- ein Los darf den Rest nicht verwerfen
            logger.error("remote_scan.lot_failed", lot_id=lot.lot_id, error=repr(exc)[:300])
            summary["errors"].append(f"Los {lot.lot_id}: {type(exc).__name__}")
            continue
        if result is not None:
            evaluated.append(result)

    evaluated.sort(key=lambda item: (item.can_bid_now, item.expected_profit_current or 0), reverse=True)
    dumps = [item.model_dump() for item in evaluated]
    summary["discovered"] = len(dumps)
    await save_scan(PLATFORM, dumps, summary["errors"])
    await _notify_new_results(PLATFORM, dumps, summary, await _telegram_configured())
    logger.info("remote_scan.evaluated", **{k: v for k, v in summary.items() if k != "skipped"})
    return summary


def catawiki_scan_due(frequency: str | None, now: datetime | None = None) -> bool:
    # Ohne Einstellung aus: Catawiki antwortet dem Scraper mit 403, und der
    # Parser ist nie gegen echte Los-Seiten gelaufen.
    frequency = (frequency or "off").strip().lower()
    if frequency not in {"daily", "weekly", "off"}:
        raise ValueError("Catawiki-Intervall muss daily, weekly oder off sein")
    now = (now or datetime.now(ZoneInfo("Europe/Berlin"))).astimezone(ZoneInfo("Europe/Berlin"))
    return frequency == "daily" or (frequency == "weekly" and now.weekday() == 6)
