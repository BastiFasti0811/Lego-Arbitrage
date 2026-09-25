"""Catawiki-Scan ueber einen Heimrechner.

Akamai sperrt catawiki.com fuer die IP des Prod-Servers (403 auf Seiten und
JSON-APIs, geprueft am 25.09.2026). Ein Heimanschluss kommt durch. Deshalb
liest ein Skript auf Sebastians PC (app/tools/catawiki_home_scan.py) die Lose
und liefert sie hier ab; Bewertung, Speicherung und Telegram bleiben auf Prod.

Ablauf:
1. Der Heimrechner fragt alle paar Minuten `GET /api/remote-scan/runner/job`.
   Die Antwort sagt, ob ein Scan faellig ist (Button in der App oder Zeitplan
   `catawiki_scan_frequency`) und mit welchen URLs.
2. Er scannt und schickt die Lose an `POST /api/remote-scan/runner/results`.
3. Ein Celery-Task bewertet sie wie der fruehere Server-Scan.

Der Heimrechner meldet sich mit dem Token aus der Einstellung
`remote_scan_token`, nicht mit dem App-Passwort. Das Token oeffnet nur diese
beiden Endpunkte.
"""

import hmac
from datetime import UTC, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, AuctionScanState
from app.runtime_settings import get_settings_map
from app.services.catawiki import CatawikiLotCandidate, CatawikiParseError, canonical_lot_url

BERLIN = ZoneInfo("Europe/Berlin")
PLATFORM = "CATAWIKI"
TOKEN_KEY = "remote_scan_token"
# Interne Zustaende, nicht in der Einstellungsliste sichtbar (Kategorie "internal").
INTERNAL_CATEGORY = "internal"
REQUESTED_KEY = "catawiki_scan_requested_at"
RUNNER_SEEN_KEY = "catawiki_runner_seen_at"
# Wie der fruehere Beat-Termin des Server-Scans.
SCHEDULED_TIME = time(8, 40)
MIN_TOKEN_LENGTH = 24

Condition = Literal["NEW_SEALED", "NEW_OPEN_BOX", "USED_COMPLETE", "USED_INCOMPLETE", "UNKNOWN"]


def verify_runner_token(provided: str | None, expected: str | None) -> bool:
    """Konstantzeit-Vergleich; ein fehlendes oder zu kurzes Token oeffnet nichts."""
    if not provided or not expected or len(expected) < MIN_TOKEN_LENGTH:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


class RemoteLot(BaseModel):
    """Ein vom Heimrechner gelesenes Los (Felder von CatawikiLotCandidate)."""

    lot_id: str = Field(pattern=r"^\d{1,12}$")
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(max_length=500)
    category_url: str = Field(max_length=500)
    current_bid: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    shipping_eur: float | None = Field(default=None, ge=0, le=10_000, allow_inf_nan=False)
    set_numbers: list[str] = Field(default_factory=list, max_length=10)
    condition: Condition = "UNKNOWN"
    box_damage: bool = False
    is_closed: bool = False
    details_verified: bool = False
    buyer_fee_rate: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    buyer_fee_fixed: float | None = Field(default=None, ge=0, le=1_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def _url_must_be_this_catawiki_lot(self):
        # Kein fremder Link darf ueber den Heimrechner in Scan und Telegram gelangen.
        try:
            canonical = canonical_lot_url(self.url)
        except CatawikiParseError as exc:
            raise ValueError(str(exc)) from exc
        if canonical.rsplit("/", 1)[-1] != self.lot_id:
            raise ValueError("Losnummer passt nicht zur URL")
        self.url = canonical
        if any(not number.isdigit() or not 3 <= len(number) <= 7 for number in self.set_numbers):
            raise ValueError("Ungueltige Setnummer")
        return self

    def to_candidate(self) -> CatawikiLotCandidate:
        return CatawikiLotCandidate(
            lot_id=self.lot_id, title=self.title, url=self.url, current_bid=self.current_bid,
            shipping_eur=self.shipping_eur, set_numbers=list(self.set_numbers), condition=self.condition,
            box_damage=self.box_damage, is_closed=self.is_closed, details_verified=self.details_verified,
            buyer_fee_rate=self.buyer_fee_rate, buyer_fee_fixed=self.buyer_fee_fixed,
        )


class RemoteScanResults(BaseModel):
    lots: list[RemoteLot] = Field(default_factory=list, max_length=500)
    errors: list[str] = Field(default_factory=list, max_length=20)
    runner_version: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _short_errors(self):
        self.errors = [error[:300] for error in self.errors]
        return self


async def _get_internal(session: AsyncSession, key: str) -> datetime | None:
    row = (await session.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if not row or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


async def _set_internal(session: AsyncSession, key: str, value: datetime | None) -> None:
    row = (await session.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    text = value.isoformat() if value else None
    if row is None:
        session.add(AppSetting(key=key, value=text, is_secret=False, category=INTERNAL_CATEGORY, label=key))
    else:
        row.value = text


def _scan_urls(value: str | None) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def scheduled_scan_due(frequency: str | None, last_scan: datetime | None, now: datetime) -> bool:
    """Zeitplan: ab 08:40 Berliner Zeit an einem geplanten Tag, hoechstens einmal pro Tag."""
    from app.tasks.catawiki_scan import catawiki_scan_due

    local_now = now.astimezone(BERLIN)
    if local_now.time() < SCHEDULED_TIME or not catawiki_scan_due(frequency, now):
        return False
    return last_scan is None or last_scan.astimezone(BERLIN).date() < local_now.date()


async def runner_job(session: AsyncSession, now: datetime | None = None) -> dict:
    """Auftrag fuer den Heimrechner; merkt sich nebenbei, dass er erreichbar ist."""
    now = now or datetime.now(UTC)
    config = await get_settings_map([
        "catawiki_scan_urls", "catawiki_max_results_per_url", "catawiki_scan_frequency",
        "catawiki_user_agent", "catawiki_cookie_header",
    ])
    requested_at = await _get_internal(session, REQUESTED_KEY)
    state = (await session.execute(
        select(AuctionScanState).where(AuctionScanState.platform == PLATFORM)
    )).scalar_one_or_none()
    urls = _scan_urls(config.get("catawiki_scan_urls"))

    reason = None
    if urls and requested_at:
        reason = "requested"
    elif urls and scheduled_scan_due(config.get("catawiki_scan_frequency"), state.scanned_at if state else None, now):
        reason = "scheduled"

    await _set_internal(session, RUNNER_SEEN_KEY, now)
    await session.commit()
    try:
        max_results = max(1, min(50, int(config.get("catawiki_max_results_per_url") or 20)))
    except ValueError:
        max_results = 20
    return {
        "run": reason is not None,
        "reason": reason,
        # Immer mitliefern: `run` entscheidet, `--force` am Heimrechner braucht die URLs trotzdem.
        "scan_urls": urls,
        "max_results_per_url": max_results,
        "user_agent": config.get("catawiki_user_agent") or None,
        "cookie_header": config.get("catawiki_cookie_header") or None,
    }


async def request_scan(session: AsyncSession, now: datetime | None = None) -> None:
    await _set_internal(session, REQUESTED_KEY, now or datetime.now(UTC))
    await session.commit()


async def clear_request(session: AsyncSession) -> None:
    await _set_internal(session, REQUESTED_KEY, None)
    await session.commit()


async def scan_status(session: AsyncSession) -> dict:
    token = (await session.execute(select(AppSetting).where(AppSetting.key == TOKEN_KEY))).scalar_one_or_none()
    state = (await session.execute(
        select(AuctionScanState).where(AuctionScanState.platform == PLATFORM)
    )).scalar_one_or_none()
    requested_at = await _get_internal(session, REQUESTED_KEY)
    runner_seen_at = await _get_internal(session, RUNNER_SEEN_KEY)
    return {
        "token_configured": bool(token and token.value and len(token.value) >= MIN_TOKEN_LENGTH),
        "requested_at": requested_at.isoformat() if requested_at else None,
        "runner_seen_at": runner_seen_at.isoformat() if runner_seen_at else None,
        "last_scan": {
            "scanned_at": state.scanned_at.isoformat(),
            "status": state.status,
            "lots": len(state.results or []),
            "errors": state.errors or [],
        } if state else None,
    }
