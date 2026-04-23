"""Re-evaluate watched auction lots on a schedule."""

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from app.models import AuctionWatchItem, LegoSet
from app.models.base import async_session
from app.notifications.telegram_bot import send_auction_watch_alert
from app.services.auction_watch import evaluate_auction
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


def _run_async(coro):
    return asyncio.run(coro)


@celery_app.task(name="app.tasks.auction_watch.refresh_auction_watchlist")
def refresh_auction_watchlist() -> dict:
    return _run_async(_refresh_auction_watchlist_async())


async def _refresh_auction_watchlist_async() -> dict:
    summary = {"checked": 0, "under_limit": 0, "alerts_sent": 0, "errors": 0}

    async with async_session() as session:
        result = await session.execute(
            select(AuctionWatchItem, LegoSet)
            .join(LegoSet, AuctionWatchItem.set_id == LegoSet.id)
            .where(AuctionWatchItem.is_active)
        )

        for item, lego_set in result.all():
            try:
                evaluation = await evaluate_auction(
                    set_number=lego_set.set_number,
                    current_bid=item.current_bid,
                    purchase_shipping=item.purchase_shipping,
                    source_url=item.source_url,
                    source_platform=item.source_platform,
                    desired_roi_percent=item.desired_roi_percent,
                    buyer_fee_rate=item.buyer_fee_rate,
                    buyer_fee_fixed=item.buyer_fee_fixed,
                    fee_applies_to_shipping=item.fee_applies_to_shipping,
                    set_name=lego_set.set_name,
                    theme=lego_set.theme,
                    release_year=lego_set.release_year,
                    uvp=lego_set.uvp_eur,
                    eol_status=lego_set.eol_status,
                )

                item.max_bid = evaluation.bid_result.max_bid
                item.break_even_bid = evaluation.bid_result.break_even_bid
                item.bid_gap = evaluation.current_bid_gap
                item.bid_status = evaluation.bid_status
                item.recommendation_text = evaluation.recommendation_text
                item.expected_roi_current = evaluation.expected_roi_at_current_bid
                item.expected_roi_target = evaluation.bid_result.expected_roi_at_max_bid
                item.expected_profit_current = evaluation.expected_profit_at_current_bid
                item.expected_profit_target = evaluation.bid_result.expected_profit_at_max_bid
                item.all_in_cost_current = evaluation.current_total_purchase_cost
                item.all_in_cost_target = evaluation.bid_result.total_purchase_cost_at_max_bid
                item.buyer_fee_current = evaluation.current_buyer_fee
                item.buyer_fee_target = evaluation.bid_result.buyer_fee_at_max_bid
                item.market_price = evaluation.analysis.market_consensus.consensus_price
                item.reference_price = evaluation.analysis.reference_price
                item.reference_label = evaluation.analysis.reference_label
                item.set_category = evaluation.analysis.category
                item.eol_status = evaluation.eol_status
                item.warning_text = evaluation.warnings[0] if evaluation.warnings else None
                item.last_checked_at = datetime.now(timezone.utc)
                item.check_count = (item.check_count or 0) + 1
                item.status = "ACTIVE" if evaluation.can_bid_now else "OVER_LIMIT"

                summary["checked"] += 1

                if evaluation.can_bid_now:
                    summary["under_limit"] += 1
                    should_alert = (
                        item.last_alerted_at is None
                        or item.last_alerted_at < datetime.now(timezone.utc) - timedelta(hours=20)
                    )
                    if should_alert:
                        sent = await send_auction_watch_alert(item, lego_set.set_number, lego_set.set_name)
                        if sent:
                            item.last_alerted_at = datetime.now(timezone.utc)
                            summary["alerts_sent"] += 1
            except Exception as exc:
                summary["errors"] += 1
                logger.error("auction_watch.refresh_failed", item_id=item.id, error=str(exc))

        await session.commit()

    logger.info("auction_watch.refresh_complete", **summary)
    return summary
