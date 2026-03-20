"""Telegram Bot for deal notifications.

Sends alerts when profitable deals are found.
Supports inline buttons for quick actions.
"""

import asyncio

import structlog
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.config import settings
from app.engine.decision_engine import AnalysisResult, Recommendation

logger = structlog.get_logger()


def _format_deal_message(analysis: AnalysisResult) -> str:
    """Format analysis result as Telegram message."""

    # Recommendation emoji
    rec_emoji = {
        Recommendation.GO_STAR: "🌟 GO ⭐",
        Recommendation.GO: "✅ GO",
        Recommendation.CHECK: "🔍 PRÜFEN",
        Recommendation.NO_GO: "❌ NO-GO",
    }.get(analysis.recommendation, "❓")

    # Risk color
    risk_emoji = {
        "green": "🟢",
        "yellow": "🟡",
        "orange": "🟠",
        "red": "🔴",
    }.get(analysis.risk.color, "⚪")

    msg = (
        f"🧱 *LEGO {analysis.set_number}*\n"
        f"_{analysis.set_name}_ ({analysis.release_year})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 *Marktdaten*\n"
    )

    if analysis.uvp:
        msg += f"UVP: {analysis.uvp:.0f}€\n"
    msg += (
        f"Marktpreis: {analysis.market_consensus.consensus_price:.0f}€ "
        f"({analysis.market_consensus.num_sources} Quellen)\n"
        f"Angebot: *{analysis.offer_price:.0f}€*\n"
    )
    if analysis.discount_vs_uvp:
        msg += f"Rabatt vs UVP: {analysis.discount_vs_uvp:.0f}%\n"

    msg += (
        f"\n💰 *ROI-Kalkulation*\n"
        f"Einkauf Total: {analysis.roi.total_purchase_cost:.0f}€\n"
        f"Verkauf Netto: {analysis.roi.net_revenue:.0f}€\n"
        f"*Gewinn: {analysis.roi.net_profit:+.0f}€*\n"
        f"*ROI: {analysis.roi.roi_percent:.1f}%* "
        f"(Jahres-ROI: {analysis.roi.annualized_roi:.1f}%)\n"
    )

    msg += (
        f"\n⚠️ *Risiko*\n"
        f"Set-Alter: {analysis.set_age}J → {analysis.category}\n"
        f"Risk-Score: {risk_emoji} {analysis.risk.total}/10 ({analysis.risk.rating})\n"
    )

    msg += (
        f"\n🎯 *Empfehlung: {rec_emoji}*\n"
        f"{analysis.reason}\n"
    )

    for suggestion in analysis.suggestions[:2]:
        msg += f"💡 {suggestion}\n"

    if analysis.market_consensus.warnings:
        msg += "\n⚠️ " + " | ".join(analysis.market_consensus.warnings[:2])

    return msg


async def send_deal_alert(analysis: AnalysisResult, offer_url: str | None = None) -> bool:
    """Send a deal alert via Telegram.

    Returns True if sent successfully.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("telegram.not_configured")
        return False

    # Filter: only send GO recommendations if configured
    if settings.telegram_alert_on_go_only:
        if analysis.recommendation not in (Recommendation.GO_STAR, Recommendation.GO):
            return False

    try:
        bot = Bot(token=settings.telegram_bot_token)
        message = _format_deal_message(analysis)

        # Inline keyboard with action buttons
        keyboard = []
        if offer_url:
            keyboard.append([InlineKeyboardButton("🔗 Angebot öffnen", url=offer_url)])
        keyboard.append([
            InlineKeyboardButton("📊 Details", callback_data=f"detail_{analysis.set_number}"),
            InlineKeyboardButton("👁️ Watchlist", callback_data=f"watch_{analysis.set_number}"),
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

        logger.info("telegram.sent", set_number=analysis.set_number, recommendation=analysis.recommendation)
        return True

    except Exception as e:
        logger.error("telegram.send_failed", error=str(e))
        return False


async def send_daily_summary(
    deals_found: int,
    go_deals: int,
    best_deal: AnalysisResult | None = None,
    total_potential_profit: float = 0,
) -> bool:
    """Send daily summary via Telegram."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False

    try:
        bot = Bot(token=settings.telegram_bot_token)

        msg = (
            f"📋 *LEGO Arbitrage — Tagesbericht*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Angebote gescannt: {deals_found}\n"
            f"GO-Deals gefunden: {go_deals}\n"
            f"Potentieller Gewinn: {total_potential_profit:.0f}€\n"
        )

        if best_deal:
            msg += (
                f"\n🏆 *Bester Deal:*\n"
                f"LEGO {best_deal.set_number} — {best_deal.set_name}\n"
                f"ROI: {best_deal.roi.roi_percent:.1f}% | "
                f"Gewinn: {best_deal.roi.net_profit:.0f}€\n"
            )

        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=msg,
            parse_mode=ParseMode.MARKDOWN,
        )
        return True

    except Exception as e:
        logger.error("telegram.summary_failed", error=str(e))
        return False
