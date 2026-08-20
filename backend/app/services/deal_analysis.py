"""Deal-analysis use case and infrastructure adapters."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.metadata import merge_set_info, needs_metadata_retry
from app.domain.platforms import detect_source_platform
from app.engine.decision_engine import AnalysisResult, analyze_deal
from app.models import DealFeedback, LegoSet
from app.scrapers import (
    METADATA_SCRAPERS,
    AmazonScraper,
    BrickEconomyScraper,
    BrickMergeScraper,
    EbaySoldScraper,
    LegoComScraper,
)
from app.scrapers.base import ScrapedPrice

logger = structlog.get_logger()


@dataclass(frozen=True)
class DealAnalysisCommand:
    set_number: str
    offer_price: float
    condition: str = "NEW_SEALED"
    box_damage: bool = False
    purchase_shipping: float | None = None
    source_url: str | None = None
    source_platform: str | None = None
    set_name: str | None = None
    theme: str | None = None
    release_year: int | None = None
    uvp: float | None = None
    eol_status: str | None = None


@dataclass(frozen=True)
class MarketContext:
    prices: list[ScrapedPrice]
    set_name: str
    theme: str
    release_year: int
    uvp: float | None
    eol_status: str
    monthly_sales: int | None
    still_in_retail: bool
    detected_platform: str | None


@dataclass(frozen=True)
class DealAnalysisOutcome:
    analysis: AnalysisResult
    context: MarketContext
    calibration_roi_delta: float | None
    calibration_sample_size: int
    calibrated_roi_percent: float
    suggestions: list[str]


class MarketContextProvider(Protocol):
    async def load(self, command: DealAnalysisCommand) -> MarketContext:
        """Load market data and metadata for the command."""


class AnalysisRepository(Protocol):
    async def feedback_calibration(self) -> tuple[float | None, int]:
        """Return ROI calibration delta and sample size."""

    async def upsert_set_from_analysis(self, analysis: AnalysisResult, eol_status: str) -> None:
        """Persist the latest set metadata and market snapshot."""


class ScraperMarketContextProvider:
    """Market context provider backed by the configured scrapers."""

    async def load(self, command: DealAnalysisCommand) -> MarketContext:
        prices: list[ScrapedPrice] = []
        set_name = command.set_name or f"LEGO {command.set_number}"
        theme = command.theme or "Unknown"
        release_year = command.release_year or 2020
        uvp = command.uvp
        eol_status = command.eol_status or "UNKNOWN"

        async def scrape_source(scraper_cls, set_number: str):
            try:
                async with scraper_cls() as scraper:
                    info = await scraper.get_set_info(set_number)
                    price = await scraper.get_price(set_number)
                    return info, price
            except Exception as exc:
                logger.warning("analysis.scraper_failed", scraper=scraper_cls.__name__, error=str(exc))
                return None, None

        scrapers = [
            BrickEconomyScraper,
            BrickMergeScraper,
            EbaySoldScraper,
                    AmazonScraper,
            LegoComScraper,
        ]
        results = await asyncio.gather(
            *[scrape_source(scraper, command.set_number) for scraper in scrapers],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                continue
            info, price = result
            if info:
                set_name, theme, release_year, uvp, eol_status = merge_set_info(
                    info=info,
                    set_number=command.set_number,
                    set_name=set_name,
                    theme=theme,
                    release_year=release_year,
                    uvp=uvp,
                    eol_status=eol_status,
                )
            if price:
                prices.append(price)

        set_name = command.set_name or set_name
        theme = command.theme or theme
        release_year = command.release_year or release_year
        uvp = command.uvp or uvp
        eol_status = command.eol_status or eol_status

        if needs_metadata_retry(theme, release_year, uvp, eol_status):
            set_name, theme, release_year, uvp, eol_status = await _retry_authoritative_metadata(
                command.set_number,
                set_name,
                theme,
                release_year,
                uvp,
                eol_status,
            )

        detected_platform = detect_source_platform(command.source_url, command.source_platform)
        still_in_retail = eol_status in ("AVAILABLE", "RETIRING_SOON")
        if not still_in_retail and detected_platform in {"AMAZON", "LEGO"}:
            still_in_retail = True
            if eol_status == "UNKNOWN":
                eol_status = "AVAILABLE"

        return MarketContext(
            prices=prices,
            set_name=set_name,
            theme=theme,
            release_year=release_year,
            uvp=uvp,
            eol_status=eol_status,
            monthly_sales=_estimate_monthly_sales(prices),
            still_in_retail=still_in_retail,
            detected_platform=detected_platform,
        )


class SqlAlchemyAnalysisRepository:
    """Analysis persistence adapter backed by SQLAlchemy sessions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def feedback_calibration(self) -> tuple[float | None, int]:
        completed = await self.session.scalar(
            select(func.count(DealFeedback.id)).where(DealFeedback.roi_deviation.isnot(None))
        )
        completed_count = completed or 0
        if completed_count < 3:
            return None, completed_count

        avg_deviation = await self.session.scalar(
            select(func.avg(DealFeedback.roi_deviation)).where(DealFeedback.roi_deviation.isnot(None))
        )
        if avg_deviation is None:
            return None, completed_count

        clamped = max(-15.0, min(15.0, float(avg_deviation)))
        return round(clamped, 1), completed_count

    async def upsert_set_from_analysis(self, analysis: AnalysisResult, eol_status: str) -> None:
        result = await self.session.execute(select(LegoSet).where(LegoSet.set_number == analysis.set_number))
        lego_set = result.scalar_one_or_none()

        if lego_set is None:
            lego_set = LegoSet(
                set_number=analysis.set_number,
                set_name=analysis.set_name,
                theme=analysis.theme,
                release_year=analysis.release_year,
                uvp_eur=analysis.uvp,
                eol_status=eol_status,
                current_market_price=(
                    analysis.market_consensus.consensus_price
                    if analysis.market_consensus.consensus_price > 0
                    else None
                ),
            )
            if lego_set.release_year:
                lego_set.category = lego_set.compute_category().value
            self.session.add(lego_set)
            await self.session.flush()
            return

        if analysis.set_name and (
            not lego_set.set_name
            or lego_set.set_name == lego_set.set_number
            or lego_set.set_name == f"LEGO {analysis.set_number}"
        ):
            lego_set.set_name = analysis.set_name
        if analysis.theme and (not lego_set.theme or lego_set.theme == "Unknown"):
            lego_set.theme = analysis.theme
        if analysis.release_year and (not lego_set.release_year or lego_set.release_year == 2020):
            lego_set.release_year = analysis.release_year
        if analysis.uvp and not lego_set.uvp_eur:
            lego_set.uvp_eur = analysis.uvp
        if eol_status and eol_status != "UNKNOWN":
            lego_set.eol_status = eol_status
        # Nur belastbare Konsenswerte werden zum gespeicherten Marktpreis —
        # dieselbe Regel wie im Scrape-Pfad, sonst schreibt der Deal-Checker
        # einen Ein-Quellen-Schaetzwert als Fakt in die Stammdaten.
        consensus = analysis.market_consensus
        if consensus.consensus_price > 0 and consensus.num_sources >= 2 and consensus.divergence_percent <= 0.30:
            from datetime import UTC, datetime

            lego_set.current_market_price = analysis.market_consensus.consensus_price
            lego_set.market_price_updated_at = datetime.now(UTC)
        if lego_set.release_year:
            lego_set.category = lego_set.compute_category().value


class DealAnalysisUseCase:
    """Coordinates market context loading, domain analysis and persistence."""

    def __init__(self, market_context: MarketContextProvider, repository: AnalysisRepository):
        self.market_context = market_context
        self.repository = repository

    async def execute(self, command: DealAnalysisCommand) -> DealAnalysisOutcome:
        context = await self.market_context.load(command)

        analysis = analyze_deal(
            set_number=command.set_number,
            set_name=context.set_name,
            release_year=context.release_year,
            theme=context.theme,
            offer_price=command.offer_price,
            prices=context.prices,
            uvp=context.uvp,
            eol_status=context.eol_status,
            condition=command.condition,
            box_damage=command.box_damage,
            monthly_sales=context.monthly_sales,
            still_in_retail=context.still_in_retail,
            purchase_shipping=command.purchase_shipping,
        )

        calibration_roi_delta, calibration_sample_size = await self.repository.feedback_calibration()
        calibrated_roi_percent = analysis.roi.roi_percent
        suggestions = list(analysis.suggestions)
        if calibration_roi_delta is not None:
            calibrated_roi_percent = round(analysis.roi.roi_percent + calibration_roi_delta, 1)
            direction = "unter" if calibration_roi_delta < 0 else "ueber"
            suggestions.insert(
                0,
                (
                    "Lern-Korrektur: echte Verkaeufe lagen zuletzt im Schnitt "
                    f"{abs(calibration_roi_delta):.1f}pp {direction} der Prognose "
                    f"({calibration_sample_size} Verkaeufe)"
                ),
            )

        await self.repository.upsert_set_from_analysis(analysis, context.eol_status)
        return DealAnalysisOutcome(
            analysis=analysis,
            context=context,
            calibration_roi_delta=calibration_roi_delta,
            calibration_sample_size=calibration_sample_size,
            calibrated_roi_percent=calibrated_roi_percent,
            suggestions=suggestions,
        )


async def _retry_authoritative_metadata(
    set_number: str,
    set_name: str,
    theme: str,
    release_year: int,
    uvp: float | None,
    eol_status: str,
) -> tuple[str, str, int, float | None, str]:
    for scraper_cls in METADATA_SCRAPERS:
        if not needs_metadata_retry(theme, release_year, uvp, eol_status):
            break
        try:
            async with scraper_cls() as scraper:
                info = await scraper.get_set_info(set_number)
            if info:
                set_name, theme, release_year, uvp, eol_status = merge_set_info(
                    info=info,
                    set_number=set_number,
                    set_name=set_name,
                    theme=theme,
                    release_year=release_year,
                    uvp=uvp,
                    eol_status=eol_status,
                )
        except Exception as exc:
            logger.warning(
                "analysis.metadata_retry_failed",
                scraper=scraper_cls.__name__,
                set_number=set_number,
                error=str(exc),
            )
    return set_name, theme, release_year, uvp, eol_status


def _estimate_monthly_sales(prices: list[ScrapedPrice]) -> int | None:
    for price in prices:
        if price.source == "EBAY_SOLD" and price.sold_count:
            return int(price.sold_count / 2)
    return None
