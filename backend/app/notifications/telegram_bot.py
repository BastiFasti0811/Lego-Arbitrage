"""Telegram Bot for deal notifications.

Sends alerts when profitable deals are found.
Supports inline buttons for quick actions.
"""

import html
import re
from dataclasses import dataclass

import structlog
from telegram import Bot, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest

from app.engine.decision_engine import AnalysisResult, Recommendation
from app.notifications.deal_actions import checker_link, deal_keyboard
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


async def send_deal_alert(
    analysis: AnalysisResult,
    offer_url: str | None = None,
    offer_id: int | None = None,
) -> bool:
    """Send a deal alert via Telegram.

    Knöpfe: siehe notifications/deal_actions. Ohne `offer_id` (z. B. ein Alert
    ohne gespeicherte Offer-Zeile) fehlen "Gekauft"/"Abgelehnt" und der Deep-Link.

    Returns True if sent successfully.
    """
    runtime_settings = await get_settings_map(
        ["telegram_bot_token", "telegram_chat_id", "telegram_alert_on_go_only", "app_public_url"]
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

    checker_url = checker_link(runtime_settings.get("app_public_url"), offer_id) if offer_id else None
    bot = Bot(token=bot_token)
    message = _format_deal_message(analysis)
    try:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=deal_keyboard(offer_id, offer_url, checker_url),
            )
        except BadRequest as e:
            # Telegram lehnt manche URL-Knöpfe ab (z. B. Hostnamen ohne Punkt).
            # Der Alert ist wichtiger als der Deep-Link: einmal ohne ihn.
            if not checker_url:
                raise
            logger.warning("telegram.deep_link_rejected", error=str(e))
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=deal_keyboard(offer_id, offer_url, None),
            )

        logger.info("telegram.sent", set_number=analysis.set_number, recommendation=analysis.recommendation)
        return True

    except Exception as e:
        logger.error("telegram.send_failed", error=str(e))
        return False


@dataclass
class DailyBestDeal:
    """Bestes GO-Angebot des Tages, aus der gespeicherten Offer-Zeile.

    Die volle Analyse wird nicht nachgerechnet: ROI und Empfehlung stehen am
    Angebot, der Preis ist der Angebotspreis plus Versand.
    """

    set_number: str
    set_name: str
    recommendation: str
    roi_percent: float | None
    price: float
    platform: str
    url: str | None = None


def _de_number(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_daily_summary(
    offers_rated: int,
    go_deals: int,
    best_deal: DailyBestDeal | None = None,
    deals_this_week: int | None = None,
    deals_goal: int | None = None,
) -> str:
    """HTML: Setnamen enthalten Klammern und Unterstriche, die Markdown zerlegt."""
    lines = [
        "📋 <b>LEGO Arbitrage — Tagesbericht</b> (letzte 24 h)",
        "",
        f"Angebote bewertet: {offers_rated}",
        f"GO-Deals: {go_deals}",
    ]
    if deals_this_week is not None and deals_goal:
        lines.append(f"Geprüfte Deals diese Woche: {deals_this_week}/{deals_goal}")
    if best_deal:
        star = " ⭐" if best_deal.recommendation == Recommendation.GO_STAR else ""
        roi = f"ROI {_de_number(best_deal.roi_percent, 1)} %" if best_deal.roi_percent is not None else "ROI ?"
        name = html.escape(f"LEGO {best_deal.set_number} — {best_deal.set_name}")
        lines += [
            "",
            f"🏆 <b>Bester Deal{star}</b>",
            f'<a href="{html.escape(best_deal.url, quote=True)}">{name}</a>' if best_deal.url else name,
            f"{roi} · {_de_number(best_deal.price)} € · {html.escape(best_deal.platform)}",
        ]
    return "\n".join(lines)


async def send_daily_summary(
    offers_rated: int,
    go_deals: int,
    best_deal: DailyBestDeal | None = None,
    deals_this_week: int | None = None,
    deals_goal: int | None = None,
) -> bool:
    """Send daily summary via Telegram."""
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")

    if not bot_token or not chat_id:
        return False

    try:
        bot = Bot(token=bot_token)
        await bot.send_message(
            chat_id=chat_id,
            text=format_daily_summary(offers_rated, go_deals, best_deal, deals_this_week, deals_goal),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
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
        price_label = "Preis" if item.get("source_platform") == "BRICKLINK" else "Gebot"
        lines.append(
            f"{item.get('source_platform', 'AUCTION')} | LEGO {item['set_number']} | "
            f"{price_label} {item['current_bid']:.2f} EUR | Max {item['recommended_max_bid']:.2f} EUR"
        )
        if item.get("all_in_cost_current") is not None:
            lines.append(f"Inklusive Gebuehren und Versand: {item['all_in_cost_current']:.2f} EUR")
        if item.get("source_url"):
            lines.append(item["source_url"])
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


# ── Montags-Paket: fertige Inserate der Woche ────────────────────────
# HTML statt Markdown: Anzeigentexte enthalten *, _ und Klammern, die Markdown
# zerlegen wuerde. <code>/<pre> lassen sich in Telegram per Tipp kopieren.
_TELEGRAM_TEXT_LIMIT = 4096
_MEDIA_GROUP_LIMIT = 10


def format_listing_pack_header(count: int, unlisted_total: int) -> str:
    if count == 0:
        return (
            "🧱 <b>Inserate der Woche</b>\n\n"
            f"Kein Posten bereit. Nicht inseriert: {unlisted_total} (ohne Preisvorschlag oder schon vorbereitet)."
        )
    return (
        f"🧱 <b>{count} Inserate der Woche</b> (nicht inseriert: {unlisted_total})\n\n"
        "Je Posten: Fotos, Titel und Text zum Kopieren. Nach dem Einstellen in der App "
        "unter <i>Listings</i> die Anzeigen-URL einfügen und „Aktiv schalten“."
    )


def format_listing_pack_entry(index: int, count: int, entry) -> str:
    """Ein Posten als HTML-Nachricht, sicher unter Telegrams 4096-Zeichen-Grenze."""
    price_suffix = " VB" if entry.price_type == "VB" else ""
    facts = [f"{entry.price:.0f} €{price_suffix}"]
    if entry.storage_location:
        facts.append(f"📦 {html.escape(entry.storage_location)}")
    facts.append(f"{len(entry.photo_paths)} Fotos" if entry.photo_paths else "⚠️ keine Fotos")
    if entry.text_source == "VORLAGE":
        facts.append("Vorlagentext (KI war nicht erreichbar)")
    head = (
        f"<b>{index}/{count} · {html.escape(entry.label)}</b>\n"
        f"{' · '.join(facts)}\n\n"
        f"<code>{html.escape(entry.title)}</code>\n\n"
    )
    budget = _TELEGRAM_TEXT_LIMIT - len(head) - len("<pre></pre>") - 20
    body = html.escape(entry.body)
    if len(body) > budget:
        body = body[: budget - 1].rsplit(" ", 1)[0] + " …"
    return f"{head}<pre>{body}</pre>"


async def send_listing_pack(entries: list, unlisted_total: int) -> bool:
    """Kopf, dann je Posten Fotos (falls lesbar) und Text. True nur, wenn Kopf
    und alle Texte ankamen — fehlende Fotos allein sind kein Zustellfehler."""
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")
    if not bot_token or not chat_id:
        return False

    bot = Bot(token=bot_token)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=format_listing_pack_header(len(entries), unlisted_total),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error("telegram.listing_pack_failed", stage="header", error=str(e))
        return False

    all_sent = True
    for index, entry in enumerate(entries, start=1):
        photos = entry.photo_paths[:_MEDIA_GROUP_LIMIT]
        if photos:
            try:
                media = [InputMediaPhoto(path.read_bytes()) for path in photos]
                await bot.send_media_group(chat_id=chat_id, media=media)
            except Exception as e:
                logger.warning("telegram.listing_pack_photos_failed", item_id=entry.item_id, error=str(e))
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=format_listing_pack_entry(index, len(entries), entry),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            all_sent = False
            logger.error("telegram.listing_pack_failed", stage="entry", item_id=entry.item_id, error=str(e))
    logger.info("telegram.listing_pack_sent", items=len(entries), complete=all_sent)
    return all_sent


# ── Wochen-Digest: Abstand zu den Zielzahlen ────────────────────────


def _trend(now: int, before: int) -> str:
    diff = now - before
    if diff > 0:
        return f"▲ +{diff}"
    if diff < 0:
        return f"▼ −{-diff}"
    return "= ±0"


def _pace(progress, missing: int) -> str:
    if missing == 0:
        return "erreicht ✅"
    per_week = progress.per_week_needed(missing)
    if per_week is None:
        return f"fehlen {missing}, Frist vorbei"
    return f"fehlen {missing}, ≈ {_de_number(per_week, 1)}/Woche"


def format_goal_digest(digest) -> str:
    """Montags-Digest als HTML. `digest` ist ein goal_progress.GoalDigest."""
    progress = digest.progress
    week, deals, before_deals = digest.reported, digest.reported.deals, digest.before.deals
    sales, before_sales = digest.reported.sales, digest.before.sales
    goal_deals = progress.goal_deals_per_week
    hit = "✅" if deals.total >= goal_deals else "⚠️"

    lines = [
        f"🎯 <b>Wochen-Digest {week.week.label}</b>",
        f"Frist {progress.deadline:%d.%m.}: noch {progress.days_left} Tage",
        "",
        f"<b>Zukauf</b> (Ziel {goal_deals} geprüfte Deals/Woche)",
        f"{hit} {deals.total}/{goal_deals} · Vorwoche {before_deals.total} ({_trend(deals.total, before_deals.total)})",
        f"GO⭐ {deals.go_star} · GO {deals.go} · Auktion freigegeben {deals.auctions}",
    ]
    if deals.checks:
        lines.append(f"🔍 Grenzfälle (CHECK, nicht gemeldet): {deals.checks}")
    if deals.bought or deals.rejected:
        lines.append(f"🛒 Entschieden: gekauft {deals.bought} · abgelehnt {deals.rejected}")
    lines += [
        "",
        "<b>Verkauf</b> (Lego-Posten)",
        f"📦 Inseriert {progress.lego_listed}/{progress.lego_items} · Ziel {progress.goal_listed}: "
        f"{_pace(progress, progress.listed_missing)}",
        f"Woche +{sales.listed} · Vorwoche +{before_sales.listed} ({_trend(sales.listed, before_sales.listed)})",
        f"💶 Verkauft {progress.lego_sold}/{progress.goal_sold}: {_pace(progress, progress.sold_missing)}",
        f"Woche +{sales.sold} · Vorwoche +{before_sales.sold} ({_trend(sales.sold, before_sales.sold)})",
    ]
    return "\n".join(lines)


async def send_goal_digest(digest) -> bool:
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")
    if not bot_token or not chat_id:
        return False
    try:
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=format_goal_digest(digest), parse_mode=ParseMode.HTML)
        logger.info("telegram.goal_digest_sent")
        return True
    except Exception as e:
        logger.error("telegram.goal_digest_failed", error=str(e))
        return False


# ── Tages-Check: faellige Preisanpassungen ──────────────────────────

_LISTING_PLATFORM_SHORT = {"KLEINANZEIGEN": "KA", "EBAY": "eBay"}


def _listing_link(entry) -> str:
    label = html.escape(entry.label)
    if entry.url and entry.url.startswith(("https://", "http://")):
        return f'<a href="{html.escape(entry.url, quote=True)}">{label}</a>'
    return label


def _listing_facts(entry) -> str:
    platform = _LISTING_PLATFORM_SHORT.get(entry.platform, entry.platform)
    days = f", {entry.days_online} T online" if entry.days_online is not None else ""
    return f"{platform}{days}"


def _price(value: float | None) -> str:
    if value is None:
        return "?"
    return _de_number(value, 0 if float(value).is_integer() else 2)


def format_listing_check(plan) -> str:
    """Sammelnachricht des Tages-Checks, sicher unter Telegrams 4096-Zeichen-Grenze.

    Ausgefuehrt wird von Hand (ADR 0002): die Nachricht nennt nur, was zu tun ist."""
    blocks: list[list[str]] = []
    if plan.suggestions:
        count = len(plan.suggestions)
        head = f"🏷️ <b>{count} Preisanpassung{'en' if count != 1 else ''} fällig</b>"
        if plan.new_suggestions and plan.new_suggestions < count:
            head += f" ({plan.new_suggestions} neu)"
        lines = [head]
        for entry in plan.suggestions:
            new = "🆕 " if entry.is_new else ""
            lines.append(
                f"• {new}{_listing_link(entry)} ({_listing_facts(entry)}): "
                f"{_price(entry.current_price)} → <b>{_price(entry.suggested_price)} €</b>"
                + (f"\n  <i>{html.escape(entry.reason)}</i>" if entry.reason else "")
            )
        blocks.append(lines)
    if plan.floor_hints:
        lines = ["🧱 <b>An der Schmerzgrenze</b> (Hinweis kommt nur einmal)"]
        for entry in plan.floor_hints:
            lines.append(
                f"• {_listing_link(entry)} ({_listing_facts(entry)}): {_price(entry.current_price)} € "
                "= Schmerzgrenze. Schmerzgrenze senken, Text/Fotos auffrischen oder beenden."
            )
        blocks.append(lines)
    if plan.sold_leftovers:
        lines = ["🪦 <b>Verkauft, Anzeige läuft noch</b>"]
        for entry in plan.sold_leftovers:
            lines.append(f"• {_listing_link(entry)} ({_listing_facts(entry)}): Anzeige löschen und in der App beenden.")
        blocks.append(lines)
    footer = "In der App unter <i>Anpassungen</i>: Übernommen, Später (3 Tage) oder Verwerfen."
    if plan.snoozed:
        footer += f" Zurückgestellt: {plan.snoozed}."

    text = ""
    omitted = 0
    for lines in blocks:
        for line in lines:
            candidate = f"{text}\n{line}" if text else line
            if len(candidate) + len(footer) + 60 > _TELEGRAM_TEXT_LIMIT:
                omitted += 1
                continue
            text = candidate
        text += "\n"
    if omitted:
        text += f"… und {omitted} weitere in der App.\n"
    return f"{text}\n{footer}".strip()


async def send_listing_check(plan) -> bool:
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    bot_token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")
    if not bot_token or not chat_id:
        return False
    try:
        bot = Bot(token=bot_token)
        await bot.send_message(
            chat_id=chat_id,
            text=format_listing_check(plan),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info(
            "telegram.listing_check_sent",
            suggestions=len(plan.suggestions),
            floor_hints=len(plan.floor_hints),
            sold_leftovers=len(plan.sold_leftovers),
        )
        return True
    except Exception as e:
        logger.error("telegram.listing_check_failed", error=str(e))
        return False
