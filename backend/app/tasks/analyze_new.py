"""Analysis tasks — evaluate new offers and send notifications."""

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select, and_

from app.engine.decision_engine import Recommendation, analyze_deal
from app.models import LegoSet, Offer, PriceRecord
from app.models.base import async_session
from app.notifications.telegram_bot import send_deal_alert, send_daily_summary
from app.scrapers.base import ScrapedPrice
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


def _run_async(coro):
    return asyncio.run(coro)


@celery_app.task(name="app.tasks.analyze_new.analyze_new_offers")
def analyze_new_offers() -> dict:
    """Analyze all unanalyzed offers. Runs every 30 minutes."""
    return _run_async(_analyze_new_async())


async def _analyze_new_async() -> dict:
    """Async implementation: analyze offers that haven't been evaluated yet."""
    summary = {"analyzed": 0, "go_deals": 0, "notifications_sent": 0}

    async with async_session() as session:
        # Find offers without analysis
        result = await session.execute(
            select(Offer, LegoSet)
            .join(LegoSet, Offer.set_id == LegoSet.id)
            .where(
                and_(
                    Offer.recommendation.is_(None),
                    Offer.status == "ACTIVE",
                )
            )
            .limit(100)
        )

        for offer, lego_set in result.all():
            try:
                # Get recent prices for this set
                price_result = await session.execute(
                    select(PriceRecord)
                    .where(PriceRecord.set_id == lego_set.id)
                    .where(PriceRecord.scraped_at > datetime.now(timezone.utc) - timedelta(days=7))
                    .order_by(PriceRecord.scraped_at.desc())
                )
                db_prices = price_result.scalars().all()

                # Convert to ScrapedPrice objects
                scraped_prices = [
                    ScrapedPrice(
                        source=p.source,
                        price_eur=p.price_eur,
                        median_price=p.median_price,
                        sold_count=p.sold_count,
                        is_reliable=p.is_reliable,
                    )
                    for p in db_prices
                ]

                # Run analysis
                analysis = analyze_deal(
                    set_number=lego_set.set_number,
                    set_name=lego_set.set_name,
                    release_year=lego_set.release_year,
                    theme=lego_set.theme,
                    offer_price=offer.price_eur,
                    prices=scraped_prices,
                    uvp=lego_set.uvp_eur,
                    eol_status=lego_set.eol_status,
                    condition=offer.condition,
                    box_damage=offer.box_damage,
                    purchase_shipping=offer.shipping_eur,
                )

                # Update offer with analysis results
                offer.estimated_roi = analysis.roi.roi_percent
                offer.risk_score = analysis.risk.total
                offer.recommendation = analysis.recommendation
                offer.analysis_notes = analysis.reason

                summary["analyzed"] += 1

                # Send notification for GO deals
                if analysis.recommendation in (Recommendation.GO_STAR, Recommendation.GO):
                    summary["go_deals"] += 1
                    if not offer.notified:
                        sent = await send_deal_alert(analysis, offer_url=offer.offer_url)
                        if sent:
                            offer.notified = True
                            summary["notifications_sent"] += 1

            except Exception as e:
                logger.error("analyze.offer_failed", offer_id=offer.id, error=str(e))

        await session.commit()

    logger.info("analyze.complete", **summary)
    return summary


@celery_app.task(name="app.tasks.analyze_new.send_daily_summary_task")
def send_daily_summary_task() -> dict:
    """Send daily summary of found deals. Runs at 20:00."""
    return _run_async(_send_summary_async())


async def _send_summary_async() -> dict:
    """Send daily summary via Telegram."""
    async with async_session() as session:
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)

        # Count today's analyzed offers
        result = await session.execute(
            select(Offer)
            .where(Offer.discovered_at >= datetime.combine(yesterday, datetime.min.time()))
            .where(Offer.recommendation.isnot(None))
        )
        offers = result.scalars().all()

        go_deals = [o for o in offers if o.recommendation in (Recommendation.GO_STAR, Recommendation.GO)]
        total_profit = sum(o.estimated_roi or 0 for o in go_deals)

        # Find best deal
        best = max(go_deals, key=lambda o: o.estimated_roi or 0) if go_deals else None

        # Build best deal analysis (simplified) for summary
        best_analysis = None
        # (In production, you'd reconstruct the full analysis here)

        await send_daily_summary(
            deals_found=len(offers),
            go_deals=len(go_deals),
            best_deal=best_analysis,
            total_potential_profit=total_profit,
        )

    return {"sent": True, "deals": len(offers), "go_deals": len(go_deals)}


@celery_app.task(name="app.tasks.analyze_new.retrain_model")
def retrain_model() -> dict:
    """Weekly ML model retraining with new feedback data.

    Phase 3: This will use DealFeedback data to retrain the
    price prediction model and adjust strategy parameters.
    """
    logger.info("ml.retrain_started")
    # TODO Phase 3: Implement ML retraining pipeline
    # 1. Load DealFeedback data
    # 2. Calculate prediction accuracy
    # 3. Retrain XGBoost model
    # 4. Update strategy parameters if improved
    # 5. Log metrics to MLflow
    return {"status": "placeholder", "message": "ML retraining not yet implemented (Phase 3)"}
