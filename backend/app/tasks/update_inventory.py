"""Inventory valuation and sell-signal detection tasks."""

from datetime import UTC, datetime, timedelta

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from app.domain.classification import categorize_release_year
from app.domain.identity import is_plausible_price
from app.engine.market_consensus import calculate_consensus
from app.models.base import async_session
from app.models.inventory import InventoryItem, InventoryStatus
from app.models.price import PriceSource
from app.models.set import LegoSet, SetCategory
from app.models.valuation_run import (
    ValuationRun,
    ValuationRunStatus,
    ValuationSkipReason,
    ValuationTrigger,
)
from app.scrapers import PRICE_SCRAPERS, BrickEconomyScraper, BrickMergeScraper, EbaySoldScraper
from app.services.valuation_log import (
    SourceProbe,
    ValuationRunRecorder,
    delete_runs_older_than,
)
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()

RUN_RETENTION_DAYS = 30

# price.source spricht das Vokabular des restlichen Systems (PriceSource, die
# Konsens-Gewichte); der Python-Klassenname wuerde dieselbe Quelle im Log
# unter zwei Namen fuehren, je nachdem ob sie einen Preis lieferte oder nicht.
# Nur fuer den Fehlschlag-Fall noetig: bei einem gelieferten Preis liefert
# price.source selbst schon den richtigen Namen.
_SOURCE_NAME_BY_SCRAPER: dict[type, str] = {
    EbaySoldScraper: PriceSource.EBAY_SOLD.value,
    BrickEconomyScraper: PriceSource.BRICKECONOMY.value,
    BrickMergeScraper: PriceSource.BRICKMERGE.value,
}

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

    Sieht nur den Konsens, nicht die Probes je Quelle. `num_sources == 0`
    heisst hier deshalb immer NO_PRICES — auch wenn in Wahrheit eine Quelle
    geantwortet hat und ihr Preis wegen Unplausibilitaet verworfen wurde.
    Diese Funktion kann die beiden Faelle nicht auseinanderhalten, weil sie
    die Probes nicht sieht; `_resolve_skip_reason` unten zieht die Grenze
    dort nach, wo die Probes tatsaechlich vorliegen.
    """
    if consensus.num_sources == 0:
        return "skipped", ValuationSkipReason.NO_PRICES
    if consensus.consensus_price <= 0:
        # Defensiv, nicht produktiv erreichbar: calculate_consensus zaehlt
        # nur price_eur > 0 als Quelle, daher liefert es nie num_sources >= 1
        # zusammen mit consensus_price <= 0. Der Zweig bleibt als billige
        # Absicherung des Geldpfads stehen, falls sich das je aendert — wer
        # hier einen produktiven Ausloeser sucht, wird keinen finden.
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
        name = _SOURCE_NAME_BY_SCRAPER.get(scraper_cls, scraper_cls.__name__)
        try:
            async with scraper_cls() as scraper:
                price = await scraper.get_price(item.set_number)
        except SoftTimeLimitExceeded:
            # Dieselbe Weiterreichung wie im Aufrufer (siehe dort): eine
            # Quelle, die gerade haengt, wenn Celerys weiches Limit greift,
            # ist kein toter Scraper, sondern das Signal, den Lauf zu beenden.
            raise
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
            # note=price.notes nicht vergessen: ein eingefrorener USD/EUR-Kurs
            # ist genau der Mechanismus, der einen Preis unplausibel machen
            # kann - ohne den Vermerk landet man bei "unplausibel", ohne je zu
            # erfahren, dass der Wechselkurs die eigentliche Ursache war.
            probes.append(SourceProbe(
                source=price.source, price_eur=price.price_eur,
                error=f"unplausibel gegen UVP {uvp}", note=price.notes,
            ))
            continue

        probes.append(SourceProbe(source=price.source, price_eur=price.price_eur, note=price.notes))
        prices.append(price)
    return prices, probes


def _has_rejected_price(probes: list[SourceProbe]) -> bool:
    """Ob eine Quelle eine Zahl lieferte, die es nicht in den Konsens schaffte.

    Nur aussagekraeftig, wenn `_classify_consensus` bereits NO_PRICES meldet
    (num_sources == 0) — dann heisst jede Probe mit gesetztem `price_eur`,
    dass eine Zahl geliefert wurde, die trotzdem nicht ueberlebt hat, auf
    einem von zwei Wegen: entweder `is_plausible_price` hat sie gegen die UVP
    verworfen (Probe traegt zusaetzlich `error`), oder calculate_consensus'
    eigene Ausreisser-Erkennung hat sie danach still wieder entfernt (Probe
    sah wie ein Erfolg aus, kein `error`, z. B. unter 5,00 EUR). Beides ist
    eine Ablehnung, kein Schweigen — deshalb zaehlt hier nur `price_eur`,
    nicht `error`.
    """
    return any(probe.price_eur is not None for probe in probes)


def _resolve_skip_reason(consensus, probes: list[SourceProbe]) -> tuple[str, ValuationSkipReason | None]:
    """`_classify_consensus`, ergaenzt um die eine Unterscheidung, die sie ohne
    die Probes nicht treffen kann: keine Quelle hat geantwortet (NO_PRICES)
    gegen eine Quelle hat geantwortet und ihre Zahl ist unterwegs verloren
    gegangen (IMPLAUSIBLE_PRICE) — sei es an der UVP-Pruefung in
    `_collect_prices` oder an der Ausreisser-Erkennung in
    `calculate_consensus`. Beides ergibt bei `_classify_consensus` dasselbe
    `num_sources == 0`, sind aber zwei verschiedene Handlungsanweisungen fuer
    die Person, die den Lauf liest: NO_PRICES zeigt auf die Quelle selbst
    (tot oder Set unbekannt), IMPLAUSIBLE_PRICE auf die Seite oder den
    UVP-/Ausreisser-Anker. Die Probes tragen diesen Unterschied bereits
    (siehe `_has_rejected_price`); hier wird er nur noch in den Grund
    uebersetzt.
    """
    outcome, reason = _classify_consensus(consensus)
    if reason is ValuationSkipReason.NO_PRICES and _has_rejected_price(probes):
        reason = ValuationSkipReason.IMPLAUSIBLE_PRICE
    return outcome, reason


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

        try:
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
                # Vor dem try gesetzt: schlaegt etwas NACH _collect_prices fehl
                # (der haeufige Fall, da sie tote Quellen selbst abfaengt), soll
                # record_failed unten trotzdem die schon gesammelten Probes
                # bekommen statt einer leeren Liste ohne jede Diagnose.
                probes: list[SourceProbe] = []
                try:
                    prices, probes = await _collect_prices(item, uvp_by_set.get(item.set_number))
                    consensus = calculate_consensus(prices)
                    outcome, reason = _resolve_skip_reason(consensus, probes)

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
                        signals.append(
                            f"ROI {item.unrealized_roi_percent:.0f}% hat Zielwert {roi_target:.0f}% erreicht"
                        )

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
                except SoftTimeLimitExceeded:
                    # Celerys weiches Zeitlimit ist ein Abbruchsignal fuer den
                    # GANZEN Lauf (siehe der 658s-Messwert oben am Task), keine
                    # Ausnahme eines einzelnen Items: als "failed"-Zeile
                    # aufgezeichnet und weitergemacht wuerde das Limit genau
                    # nichts bewirken. Durchreichen bis zum aeusseren Handler,
                    # der den Lauf sauber als failed abschliesst.
                    raise
                except Exception as exc:
                    logger.error("inventory.update_failed", set_number=item.set_number, error=str(exc))
                    recorder.record_failed(
                        item_id=item.id, set_number=item.set_number,
                        detail=f"{type(exc).__name__}: {exc}"[:500], probes=probes,
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
        except Exception as exc:
            # Alles ab hier laeuft in derselben Session, die gerade scheiterte
            # - SQLAlchemy rollt sie zurueck, ein weiterer Schreibversuch darauf
            # ist nicht vertrauenswuerdig. Ohne eine frische Session bliebe die
            # Zeile fuer immer "running": das Rescue am Endpoint kennt nur den
            # neuesten Lauf, und einen Beat-Lauf klickt niemand nach.
            logger.error("inventory.valuation_run_failed", run_id=run_id, error=str(exc))
            try:
                async with async_session() as failure_session:
                    failed_run = await failure_session.get(ValuationRun, run_id)
                    if failed_run is not None:
                        failed_run.status = ValuationRunStatus.FAILED.value
                        failed_run.finished_at = datetime.now(UTC)
                        failed_run.error = f"{type(exc).__name__}: {exc}"[:2000]
                        await failure_session.commit()
            except Exception as rescue_exc:
                # Das Rescue selbst darf die urspruengliche Ursache nicht
                # verschlucken: eine zweite Ausnahme hier wuerde sonst das
                # `raise` unten ersetzen, und Celery/Heartbeat saehen nur noch
                # den sekundaeren Fehler — jemand, der den toten Lauf
                # untersucht, liefe einem Symptom hinterher statt der
                # Ursache. Die Zeile bleibt dann "running" stehen, sichtbar
                # als eigener, kleinerer Befund, statt den echten Fehler zu
                # verdecken.
                logger.error(
                    "inventory.valuation_run_rescue_failed",
                    run_id=run_id, original_error=str(exc), rescue_error=str(rescue_exc),
                )
            raise

    counts = recorder.counts()
    logger.info("inventory.valuations_updated", run_id=run_id, **counts)
    return {"run_id": run_id, **counts}
