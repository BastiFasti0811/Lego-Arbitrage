"""Analysis tasks — evaluate new offers and send notifications."""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, select

from app.engine.decision_engine import Recommendation, analyze_deal
from app.models import LegoSet, Offer, PriceRecord
from app.models.base import async_session
from app.notifications.delivery import require_delivery
from app.notifications.telegram_bot import DailyBestDeal, send_daily_summary, send_deal_alert
from app.scrapers.base import ScrapedPrice
from app.services.goal_progress import DEAL_RECOMMENDATIONS, load_goal_progress
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


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
        offer_rows = result.all()
        set_ids = {lego_set.id for _offer, lego_set in offer_rows}
        prices_by_set_id: dict[int, list[PriceRecord]] = {set_id: [] for set_id in set_ids}
        if set_ids:
            price_result = await session.execute(
                select(PriceRecord)
                .where(PriceRecord.set_id.in_(set_ids))
                .where(PriceRecord.scraped_at > datetime.now(UTC) - timedelta(days=7))
                .order_by(PriceRecord.set_id, PriceRecord.scraped_at.desc())
            )
            for price_record in price_result.scalars().all():
                prices_by_set_id.setdefault(price_record.set_id, []).append(price_record)

        for offer, lego_set in offer_rows:
            try:
                db_prices = prices_by_set_id.get(lego_set.id, [])

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
    return require_delivery(_run_async(_send_summary_async()), "Tagesbericht")


def pick_best_deal(rows) -> DailyBestDeal | None:
    """Hoechster ROI unter den GO-Angeboten; GO_STAR gewinnt bei Gleichstand.

    `rows`: (Offer, LegoSet)-Paare. Ohne ROI kein Vergleich, dann zaehlt nur
    die Empfehlung.
    """
    go_rows = [(offer, lego_set) for offer, lego_set in rows if offer.recommendation in DEAL_RECOMMENDATIONS]
    if not go_rows:
        return None
    offer, lego_set = max(
        go_rows,
        key=lambda pair: (
            pair[0].estimated_roi if pair[0].estimated_roi is not None else float("-inf"),
            pair[0].recommendation == Recommendation.GO_STAR,
        ),
    )
    price = offer.total_price_eur or offer.price_eur + (offer.shipping_eur or 0)
    return DailyBestDeal(
        set_number=lego_set.set_number,
        set_name=lego_set.set_name,
        recommendation=offer.recommendation,
        roi_percent=offer.estimated_roi,
        price=price,
        platform=offer.platform,
        url=offer.offer_url,
    )


async def _send_summary_async() -> dict:
    """Tagesbericht der letzten 24 Stunden via Telegram.

    Frueher: Fenster ab gestern 00:00 UTC (bis zu 44 h), "Bester Deal" fest
    None und "Potentieller Gewinn" als Summe von ROI-Prozenten in Euro.
    """
    now = datetime.now(UTC)
    async with async_session() as session:
        result = await session.execute(
            select(Offer, LegoSet)
            .join(LegoSet, Offer.set_id == LegoSet.id)
            .where(Offer.discovered_at >= now - timedelta(hours=24))
            .where(Offer.recommendation.isnot(None))
        )
        rows = result.all()
        progress = await load_goal_progress(session, now)

    go_deals = sum(1 for offer, _ in rows if offer.recommendation in DEAL_RECOMMENDATIONS)
    best = pick_best_deal(rows)
    sent = await send_daily_summary(
        offers_rated=len(rows),
        go_deals=go_deals,
        best_deal=best,
        deals_this_week=progress.current.deals.total,
        deals_goal=progress.goal_deals_per_week,
    )
    return {"sent": sent, "deals": len(rows), "go_deals": go_deals, "best_deal": best.set_number if best else None}
