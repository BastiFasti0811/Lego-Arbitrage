"""Inventory valuation and sell-signal detection tasks."""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from app.domain.classification import categorize_release_year
from app.domain.identity import is_plausible_price
from app.engine.market_consensus import calculate_consensus
from app.models.base import async_session
from app.models.inventory import InventoryItem, InventoryStatus
from app.models.set import LegoSet, SetCategory
from app.models.valuation_run import (
    ValuationRun,
    ValuationRunStatus,
    ValuationSkipReason,
    ValuationTrigger,
)
from app.scrapers import PRICE_SCRAPERS
from app.scrapers.brickmerge import BrickMergeScraper
from app.services.valuation_log import (
    SourceProbe,
    ValuationRunRecorder,
    delete_runs_older_than,
)
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()

RUN_RETENTION_DAYS = 30

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


def _classify_consensus(consensus) -> tuple[str, ValuationSkipReason | None]:
    """Ob ein Konsens gespeichert wird — und wenn nicht, warum nicht.

    Rein und ohne I/O, damit jede der vier Aussteige-Stellen einzeln
    pruefbar ist. `is_persistable_consensus` liefert nur ja/nein; fuer das
    Protokoll wird der Grund gebraucht.
    """
    if consensus.num_sources == 0:
        return "skipped", ValuationSkipReason.NO_PRICES
    if consensus.consensus_price <= 0:
        return "skipped", ValuationSkipReason.ZERO_CONSENSUS
    if consensus.num_sources < 2:
        return "skipped", ValuationSkipReason.SINGLE_SOURCE
    if consensus.divergence_percent > 0.30:
        return "skipped", ValuationSkipReason.DIVERGENCE
    return "valued", None


async def _collect_prices(item, uvp: float | None) -> tuple[list, list[SourceProbe]]:
    """Preise aller Quellen — und je Quelle die Spur, warum sie nichts lieferte."""
    prices = []
    probes: list[SourceProbe] = []
    for scraper_cls in PRICE_SCRAPERS:
        name = scraper_cls.__name__
        try:
            async with scraper_cls() as scraper:
                price = await scraper.get_price(item.set_number)
        except Exception as exc:  # noqa: BLE001 — eine tote Quelle darf den Lauf nicht beenden
            probes.append(SourceProbe(source=name, error=f"{type(exc).__name__}: {exc}"[:200]))
            continue

        if price is None:
            probes.append(SourceProbe(source=name, error="kein Preis gefunden"))
            continue

        if not is_plausible_price(price.price_eur, uvp):
            logger.warning(
                "inventory.implausible_price",
                set_number=item.set_number, source=price.source, price=price.price_eur,
            )
            probes.append(SourceProbe(
                source=price.source, price_eur=price.price_eur,
                error=f"unplausibel gegen UVP {uvp}",
            ))
            continue

        probes.append(SourceProbe(source=price.source, price_eur=price.price_eur, note=price.notes))
        prices.append(price)
    return prices, probes


@celery_app.task(
    name="app.tasks.update_inventory.update_inventory_valuations",
    # Gemessen: 658 s fuer 41 Sets. Das globale Limit von 600 s war bereits
    # gerissen und trug nur, weil der aktuelle Worker-Pool es nicht erzwingt.
    time_limit=3600,
    soft_time_limit=3300,
)
def update_inventory_valuations(run_id: int | None = None) -> dict:
    return _run_async(_update_valuations_async(run_id))


async def _update_valuations_async(run_id: int | None = None) -> dict:
    now = datetime.utcnow()  # naive datetime to match current DB column setup
    recorder = ValuationRunRecorder()

    async with async_session() as session:
        if run_id is None:
            run = ValuationRun(
                started_at=datetime.now(UTC),
                trigger=ValuationTrigger.SCHEDULED.value,
                status=ValuationRunStatus.RUNNING.value,
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
            run_id = run.id

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
                prices, probes = await _collect_prices(item, uvp_by_set.get(item.set_number))
                consensus = calculate_consensus(prices)
                outcome, reason = _classify_consensus(consensus)

                if outcome == "skipped":
                    logger.info(
                        "inventory.valuation_skipped",
                        set_number=item.set_number, reason=reason.value,
                        sources=consensus.num_sources,
                    )
                    recorder.record_skipped(
                        item_id=item.id, set_number=item.set_number,
                        reason=reason, probes=probes,
                        consensus_price=consensus.consensus_price or None,
                    )
                    continue

                total_invested = item.buy_price + (item.buy_shipping or 0)
                item.current_market_price = round(consensus.consensus_price, 2)
                item.market_price_updated_at = now
                item.unrealized_profit = round(consensus.consensus_price - total_invested, 2)
                item.unrealized_roi_percent = (
                    round(((consensus.consensus_price - total_invested) / total_invested) * 100, 1)
                    if total_invested > 0 else 0
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
                else:
                    item.sell_signal_active = False
                    item.sell_signal_reason = None

                recorder.record_valued(
                    item_id=item.id, set_number=item.set_number,
                    consensus_price=item.current_market_price, probes=probes,
                )
            except Exception as exc:
                logger.error("inventory.update_failed", set_number=item.set_number, error=str(exc))
                recorder.record_failed(
                    item_id=item.id, set_number=item.set_number,
                    detail=f"{type(exc).__name__}: {exc}"[:500], probes=[],
                )

        await recorder.flush(session, run_id)
        run = await session.get(ValuationRun, run_id)
        if run is not None:
            run.finished_at = datetime.now(UTC)
            run.status = ValuationRunStatus.SUCCESS.value
        await delete_runs_older_than(
            session, datetime.now(UTC) - timedelta(days=RUN_RETENTION_DAYS)
        )
        await session.commit()

    counts = recorder.counts()
    logger.info("inventory.valuations_updated", run_id=run_id, **counts)
    return {"run_id": run_id, **counts}
