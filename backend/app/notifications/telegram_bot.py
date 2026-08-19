"""Telegram Bot for deal notifications.

Sends alerts when profitable deals are found.
Supports inline buttons for quick actions.
"""

import re

import structlog
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.engine.decision_engine import AnalysisResult, Recommendation
from app.runtime_settings import as_bool, get_settings_map

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
    runtime_settings = await get_settings_map(
        ["telegram_bot_token", "telegram_chat_id", "telegram_alert_on_go_only"]
    )
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")
    alert_on_go_only = as_bool(runtime_settings.get("telegram_alert_on_go_only"), default=True)

    if not bot_token or not chat_id:
        logger.warning("telegram.not_configured")
        return False

    # Filter: only send GO recommendations if configured
    if alert_on_go_only:
        if analysis.recommendation not in (Recommendation.GO_STAR, Recommendation.GO):
            return False

    try:
        bot = Bot(token=bot_token)
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
            chat_id=chat_id,
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
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")

    if not bot_token or not chat_id:
        return False

    try:
        bot = Bot(token=bot_token)

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
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.MARKDOWN,
        )
        return True

    except Exception as e:
        logger.error("telegram.summary_failed", error=str(e))
        return False


async def send_auction_watch_alert(item, set_number: str, set_name: str) -> bool:
    """Send a Telegram alert for a watched auction lot."""
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")

    if not bot_token or not chat_id:
        return False

    msg = (
        f"*Auktions-Watch*\n"
        f"LEGO {set_number} - {set_name}\n"
        f"Plattform: {item.source_platform}\n"
        f"Aktuelles Gebot: {item.current_bid:.0f} EUR\n"
        f"Maximalgebot: {(item.max_bid or 0):.0f} EUR\n"
        f"Luft: {(item.bid_gap or 0):+.0f} EUR\n"
        f"ROI jetzt: {(item.expected_roi_current or 0):.1f}%\n"
    )
    if item.recommendation_text:
        msg += f"\n{item.recommendation_text}\n"
    if item.source_url:
        msg += f"\n{item.source_url}"

    try:
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)
        return True
    except Exception as e:
        logger.error("telegram.auction_watch_failed", error=str(e))
        return False


async def send_auction_discovery_summary(discovered: list[dict]) -> bool:
    """Send a compact Telegram summary for newly scanned auction opportunities."""
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")

    if not bot_token or not chat_id or not discovered:
        return False

    platforms = sorted({item.get("source_platform", "AUCTION") for item in discovered})
    platform_label = ", ".join(platforms)
    lines = [f"*Auction Scan ({platform_label})*", f"Treffer: {len(discovered)}", ""]
    for item in discovered[:5]:
        lines.append(

                f"{item.get('source_platform', 'AUCTION')} | LEGO {item['set_number']} | "
                f"Gebot {item['current_bid']:.0f} EUR | Max {item['recommended_max_bid']:.0f} EUR"

        )
    try:
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return True
    except Exception as e:
        logger.error("telegram.auction_discovery_failed", error=str(e))
        return False


def _sanitize_markdown(text: str) -> str:
    """Strip Telegram-Markdown control chars from untrusted detail strings.

    A single unpaired underscore in a task detail made the whole consolidated
    health alert fail with 400 — and because the re-alert throttle only burns
    after a successful send, it failed identically every hour.
    """
    return re.sub(r"[_*`\[\]]", " ", text)


async def send_pipeline_health_alert(problems: list[dict]) -> bool:
    """Alert when scheduled pipeline tasks are stale or failing.

    Not subject to the GO-only filter — health alerts are always relevant.
    Each problem dict: {task_name, status, age_hours, detail}.
    """
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")

    if not bot_token or not chat_id or not problems:
        return False

    status_emoji = {"failing": "🔴", "stale": "🟠"}
    lines = ["⚠️ *Pipeline-Health-Warnung*", "Geplante Tasks melden Probleme:", ""]
    for p in problems:
        emoji = status_emoji.get(p.get("status"), "⚪")
        short_name = p["task_name"].split(".")[-1]
        age = p.get("age_hours")
        age_text = f" (seit {age:.1f}h)" if age is not None else ""
        lines.append(f"{emoji} `{short_name}` — {p.get('status')}{age_text}")
        detail = p.get("detail")
        if detail:
            lines.append(f"   ↳ {_sanitize_markdown(str(detail)[:120])}")

    try:
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        logger.info("telegram.health_alert_sent", problems=len(problems))
        return True
    except Exception as e:
        logger.error("telegram.health_alert_failed", error=str(e))
        return False


def format_weekly_report(stats: dict) -> str:
    """Weekly dead-man report text. Zero scraped prices IS the alarm, not silence."""
    prices = stats.get("prices_7d", 0)
    price_flag = "⚠️ " if prices == 0 else ""
    lines = [
        "📊 *Wochenreport LEGO-Arbitrage*",
        "",
        f"{price_flag}Preise gescraped (7 Tage): *{prices}*",
        f"Neue Angebote (7 Tage): {stats.get('offers_7d', 0)}",
        f"GO-Deals (7 Tage): {stats.get('go_7d', 0)}",
        f"Aktive Watchlist: {stats.get('watchlist_active', 0)}",
        "",
    ]
    problems = stats.get("problems") or []
    if problems:
        lines.append("🔧 Probleme:")
        lines.extend(f"• `{name.split('.')[-1]}`" for name in problems)
    else:
        lines.append("✅ Alle überwachten Tasks im Soll")
    if prices == 0:
        lines.append("")
        lines.append("⚠️ Diese Woche keine Preisdaten geschrieben — Pipeline prüfen!")
    return "\n".join(lines)


async def send_weekly_report(stats: dict) -> bool:
    """Send the weekly pipeline report — always, even when every number is zero."""
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")

    if not bot_token or not chat_id:
        return False

    try:
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=format_weekly_report(stats), parse_mode=ParseMode.MARKDOWN)
        logger.info("telegram.weekly_report_sent")
        return True
    except Exception as e:
        logger.error("telegram.weekly_report_failed", error=str(e))
        return False
