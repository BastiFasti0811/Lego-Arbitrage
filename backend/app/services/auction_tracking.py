"""Refresh the source lot before recalculating a watched auction."""

from datetime import UTC, datetime

from billiard.exceptions import SoftTimeLimitExceeded

from app.runtime_settings import get_settings_map
from app.services.auction_watch import evaluate_auction
from app.services.catawiki import CatawikiScraper, lot_review_reasons


def invalidate_watch(item, reason: str, status: str = "NEEDS_REVIEW") -> None:
    item.status = status
    item.bid_status = status
    item.recommendation_text = reason
    item.warning_text = reason
    for field in (
        "max_bid", "break_even_bid", "bid_gap", "expected_roi_current", "expected_roi_target",
        "expected_profit_current", "expected_profit_target", "all_in_cost_current", "all_in_cost_target",
        "buyer_fee_current", "buyer_fee_target", "market_price", "reference_price",
    ):
        setattr(item, field, None)


async def refresh_watch_item(item, lego_set) -> bool:
    item.last_checked_at = datetime.now(UTC)
    item.check_count = (item.check_count or 0) + 1
    condition, box_damage = "UNKNOWN", False
    if item.source_platform == "CATAWIKI":
        try:
            config = await get_settings_map(["catawiki_cookie_header", "catawiki_user_agent"])
            async with CatawikiScraper(
                cookie_header=config.get("catawiki_cookie_header"), user_agent=config.get("catawiki_user_agent"),
            ) as scraper:
                lot = await scraper.get_lot(item.source_url)
        except SoftTimeLimitExceeded:
            raise
        except Exception:
            invalidate_watch(item, "Catawiki konnte nicht aktualisiert werden. Altes Gebot nicht verwenden.")
            raise
        item.lot_title = lot.title
        if lot.current_bid is not None:
            item.current_bid = lot.current_bid
        if lot.shipping_eur is not None:
            item.purchase_shipping = lot.shipping_eur
        reasons = lot_review_reasons(lot)
        if lot.set_numbers != [lego_set.set_number]:
            reasons.append("Los passt nicht zum beobachteten Set.")
        if reasons:
            invalidate_watch(item, " ".join(reasons), "ENDED" if lot.is_closed else "NEEDS_REVIEW")
            return False
        condition, box_damage = lot.condition, lot.box_damage
        if lot.buyer_fee_rate is not None:
            item.buyer_fee_rate = lot.buyer_fee_rate
        if lot.buyer_fee_fixed is not None:
            item.buyer_fee_fixed = lot.buyer_fee_fixed

    try:
        evaluation = await evaluate_auction(
            set_number=lego_set.set_number, current_bid=item.current_bid,
            purchase_shipping=item.purchase_shipping, source_url=item.source_url,
            source_platform=item.source_platform, desired_roi_percent=item.desired_roi_percent,
            buyer_fee_rate=item.buyer_fee_rate, buyer_fee_fixed=item.buyer_fee_fixed,
            fee_applies_to_shipping=item.fee_applies_to_shipping,
            set_name=lego_set.set_name, theme=lego_set.theme, release_year=lego_set.release_year,
            uvp=lego_set.uvp_eur, eol_status=lego_set.eol_status, condition=condition, box_damage=box_damage,
        )
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        invalidate_watch(item, "Marktvergleich fehlgeschlagen. Bietempfehlung wird erst nach neuer Pruefung angezeigt.")
        raise
    bid = evaluation.bid_result
    values = {
        "max_bid": bid.max_bid, "break_even_bid": bid.break_even_bid,
        "bid_gap": evaluation.current_bid_gap, "bid_status": evaluation.bid_status,
        "recommendation_text": evaluation.recommendation_text,
        "expected_roi_current": evaluation.expected_roi_at_current_bid,
        "expected_roi_target": bid.expected_roi_at_max_bid,
        "expected_profit_current": evaluation.expected_profit_at_current_bid,
        "expected_profit_target": bid.expected_profit_at_max_bid,
        "all_in_cost_current": evaluation.current_total_purchase_cost,
        "all_in_cost_target": bid.total_purchase_cost_at_max_bid,
        "buyer_fee_current": evaluation.current_buyer_fee, "buyer_fee_target": bid.buyer_fee_at_max_bid,
        "market_price": evaluation.analysis.market_consensus.consensus_price,
        "reference_price": bid.expected_sale_price, "reference_label": "MARKT_ZUSTAND",
        "set_category": evaluation.analysis.category, "eol_status": evaluation.eol_status,
        "warning_text": " ".join(evaluation.warnings) or None,
        "status": "NEEDS_REVIEW" if evaluation.bid_status == "NEEDS_REVIEW" else (
            "ACTIVE" if evaluation.can_bid_now else "OVER_LIMIT"
        ),
    }
    for field, value in values.items():
        setattr(item, field, value)
    return evaluation.can_bid_now
