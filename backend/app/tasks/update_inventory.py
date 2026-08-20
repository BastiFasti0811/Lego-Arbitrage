"""Inventory valuation and sell-signal detection tasks."""

from datetime import datetime

import structlog
from sqlalchemy import select

from app.domain.classification import categorize_release_year
from app.domain.identity import is_plausible_price
from app.engine.market_consensus import calculate_consensus
from app.models.base import async_session
from app.models.inventory import InventoryItem, InventoryStatus
from app.models.set import LegoSet, SetCategory
from app.scrapers import PRICE_SCRAPERS
from app.scrapers.brickmerge import BrickMergeScraper
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()

OPTIMAL_HOLDING = {
    SetCategory.FRESH.value: 4.5,
    SetCategory.SWEET_SPOT.value: 12.0,
    SetCategory.ESTABLISHED.value: 24.0,
    SetCategory.VINTAGE.value: 42.0,
    SetCategory.LEGACY.value: 36.0,
}

ROI_TARGETS = {
    SetCategory.FRESH.value: 50.0,
    SetCategory.SWEET_SPOT.value: 25.0,
    SetCategory.ESTABLISHED.value: 20.0,
    SetCategory.VINTAGE.value: 30.0,
    SetCategory.LEGACY.value: 40.0,
}


def _categorize_set(release_year: int | None = None) -> str:
    if not release_year:
        return SetCategory.SWEET_SPOT.value
    return categorize_release_year(release_year)


def _detect_price_peak(history: list[dict] | None) -> bool:
    if not history or len(history) < 5:
        return False

    recent = [entry["price"] for entry in history[-5:]]
    peak = max(recent)
    peak_index = recent.index(peak)
    return peak_index < len(recent) - 1 and recent[-1] < peak * 0.97


@celery_app.task(name="app.tasks.update_inventory.update_inventory_valuations")
def update_inventory_valuations() -> dict:
    return _run_async(_update_valuations_async())


async def _update_valuations_async() -> dict:
    summary = {"updated": 0, "sell_signals": 0, "errors": 0}
    now = datetime.utcnow()  # naive datetime to match current DB column setup

    async with async_session() as session:
        set_result = await session.execute(
            select(LegoSet.set_number, LegoSet.release_year, LegoSet.uvp_eur)
        )
        set_rows = set_result.all()
        release_year_by_set = {row.set_number: row.release_year for row in set_rows}
        uvp_by_set = {row.set_number: row.uvp_eur for row in set_rows}

        result = await session.execute(
            select(InventoryItem).where(InventoryItem.status == InventoryStatus.HOLDING.value)
        )
        items = result.scalars().all()

        for item in items:
            try:
                prices = []
                for scraper_cls in PRICE_SCRAPERS:
                    try:
                        async with scraper_cls() as scraper:
                            price = await scraper.get_price(item.set_number)
                            # Gleiche Absicherung wie im Scrape-Pfad: ein
                            # Fremdprodukt-Preis schriebe hier direkt in die
                            # Geldzahlen des gehaltenen Bestands.
                            if price and not is_plausible_price(price.price_eur, uvp_by_set.get(item.set_number)):
                                logger.warning(
                                    "inventory.implausible_price",
                                    set_number=item.set_number,
                                    source=price.source,
                                    price=price.price_eur,
                                )
                                price = None
                            if price:
                                prices.append(price)
                    except Exception:
                        continue

                if not prices:
                    continue

                consensus = calculate_consensus(prices)
                if consensus.consensus_price <= 0:
                    continue

                total_invested = item.buy_price + (item.buy_shipping or 0)

                item.current_market_price = round(consensus.consensus_price, 2)
                item.market_price_updated_at = now
                item.unrealized_profit = round(consensus.consensus_price - total_invested, 2)
                item.unrealized_roi_percent = (
                    round(((consensus.consensus_price - total_invested) / total_invested) * 100, 1)
                    if total_invested > 0
                    else 0
                )

                category = _categorize_set(release_year_by_set.get(item.set_number))
                roi_target = ROI_TARGETS.get(category, 25.0)
                optimal_months = OPTIMAL_HOLDING.get(category, 12.0)
                holding_days = (now.date() - item.buy_date).days
                holding_months = holding_days / 30.44

                signals: list[str] = []
                if item.unrealized_roi_percent and item.unrealized_roi_percent >= roi_target:
                    signals.append(f"ROI {item.unrealized_roi_percent:.0f}% hat Zielwert {roi_target:.0f}% erreicht")

                if holding_months >= optimal_months:
                    signals.append(f"Optimale Haltedauer ({optimal_months:.0f} Monate) erreicht")

                try:
                    async with BrickMergeScraper() as brickmerge:
                        history = await brickmerge.get_price_history(item.set_number)
                        if _detect_price_peak(history):
                            signals.append("Marktpreis am Hochpunkt - Trend dreht")
                except Exception:
                    pass

                if signals:
                    item.sell_signal_active = True
                    item.sell_signal_reason = " | ".join(signals)
                    summary["sell_signals"] += 1
                else:
                    item.sell_signal_active = False
                    item.sell_signal_reason = None

                summary["updated"] += 1
            except Exception as exc:
                summary["errors"] += 1
                logger.error("inventory.update_failed", set_number=item.set_number, error=str(exc))

        await session.commit()

    logger.info("inventory.valuations_updated", **summary)
    return summary
