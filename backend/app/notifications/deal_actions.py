"""Knöpfe am Deal-Alert und die Antwort auf einen Klick.

Früher trug der Alert "Details" und "Watchlist" mit callback_data, aber kein
Prozess hat die Klicks je abgeholt: die Knöpfe drehten nur den Ladekreisel.
Jetzt:

- "🔗 Angebot öffnen" (URL) wie bisher;
- "🔎 Im Checker" (URL): Deep-Link in den Deal-Checker, der das Angebot lädt
  und die Analyse sofort rechnet. Nur, wenn `app_public_url` gesetzt ist;
- "✅ Gekauft" / "❌ Abgelehnt" (callback_data): speichern die Entscheidung
  (services/offer_decisions). Danach steht dort "Rückgängig".

Die Klicks holt `telegram_callbacks.poll_callbacks` im API-Prozess ab.
"""

from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models.offer_decision import DecisionKind

CALLBACK_PREFIX = "dec"
_ACTIONS = {"b": DecisionKind.BOUGHT, "r": DecisionKind.REJECTED, "u": None}
_LABELS = {
    DecisionKind.BOUGHT.value: "✅ Gekauft",
    DecisionKind.REJECTED.value: "❌ Abgelehnt",
}
# Alerts vor diesem Umbau tragen noch diese Daten.
LEGACY_PREFIXES = ("detail_", "watch_")


def checker_link(base_url: str | None, offer_id: int) -> str | None:
    """Deep-Link in den Deal-Checker; None ohne brauchbare Basisadresse."""
    if not base_url:
        return None
    base = base_url.strip().rstrip("/")
    if not base.startswith(("https://", "http://")):
        return None
    return f"{base}/checker?offer={offer_id}"


def callback_data(offer_id: int, action: str) -> str:
    return f"{CALLBACK_PREFIX}:{offer_id}:{action}"


def decision_row(offer_id: int, decision: str | None) -> list[InlineKeyboardButton]:
    """Unentschieden: beide Knöpfe. Entschieden: der Stand mit Rückgängig."""
    if decision is None:
        return [
            InlineKeyboardButton(_LABELS[DecisionKind.BOUGHT.value], callback_data=callback_data(offer_id, "b")),
            InlineKeyboardButton(_LABELS[DecisionKind.REJECTED.value], callback_data=callback_data(offer_id, "r")),
        ]
    return [
        InlineKeyboardButton(f"{_LABELS[decision]} · ↩️ rückgängig", callback_data=callback_data(offer_id, "u")),
    ]


def deal_keyboard(
    offer_id: int | None,
    offer_url: str | None,
    checker_url: str | None,
) -> InlineKeyboardMarkup | None:
    links = []
    if offer_url:
        links.append(InlineKeyboardButton("🔗 Angebot öffnen", url=offer_url))
    if checker_url:
        links.append(InlineKeyboardButton("🔎 Im Checker", url=checker_url))
    rows = [links] if links else []
    if offer_id is not None:
        rows.append(decision_row(offer_id, None))
    return InlineKeyboardMarkup(rows) if rows else None


def with_decision(markup: InlineKeyboardMarkup | None, offer_id: int, decision: str | None) -> InlineKeyboardMarkup:
    """Die URL-Zeilen der Nachricht behalten, die Entscheidungszeile tauschen."""
    rows = []
    for row in (markup.inline_keyboard if markup else ()):
        if any(button.callback_data for button in row):
            continue
        rows.append(list(row))
    rows.append(decision_row(offer_id, decision))
    return InlineKeyboardMarkup(rows)


@dataclass(frozen=True)
class ParsedAction:
    offer_id: int
    decision: DecisionKind | None  # None = rückgängig


def parse_callback(data: str | None) -> ParsedAction | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX or parts[2] not in _ACTIONS:
        return None
    try:
        offer_id = int(parts[1])
    except ValueError:
        return None
    if offer_id <= 0:
        return None
    return ParsedAction(offer_id=offer_id, decision=_ACTIONS[parts[2]])


def is_legacy(data: str | None) -> bool:
    return bool(data) and data.startswith(LEGACY_PREFIXES)


def answer_text(decision: str | None, previous: str | None, set_number: str | None) -> str:
    label = f"LEGO {set_number}" if set_number else "Angebot"
    if decision is None:
        if previous is None:
            return f"{label}: keine Entscheidung gespeichert."
        return f"{label}: Entscheidung zurückgenommen."
    if decision == DecisionKind.BOUGHT.value:
        return f"{label}: als gekauft gespeichert. Ins Inventar über „Im Checker“ → Kaufen."
    return f"{label}: abgelehnt und aus dem Live Feed genommen."
