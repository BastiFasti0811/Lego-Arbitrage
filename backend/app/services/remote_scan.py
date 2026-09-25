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
`remote_scan_token`, nicht mit dem App-Passwort. Das Token oeffnet nur die
Endpunkte unter /api/remote-scan/runner/. Achtung: der Auftrag enthaelt den
optionalen Catawiki-Cookie-Header aus den Einstellungen; wer das Token hat,
bekommt also auch diese Catawiki-Sitzung.

Auftrags-Sperre: Prod gibt immer nur einen Auftrag mit eigener ID aus und nimmt
Ergebnisse genau einmal fuer genau diesen Auftrag an. Bis der Bewertungs-Task
fertig ist (finish_job) oder JOB_LEASE abgelaufen ist, gibt es keinen neuen
Auftrag. Weil der Task auf 30 Minuten begrenzt ist und die Sperre 60 Minuten
haelt, laufen nie zwei Bewertungen parallel, und dieselben Lose gehen nicht
doppelt per Telegram raus.
"""

import hmac
import json
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, AuctionScanState
from app.runtime_settings import get_settings_map
from app.services.catawiki import PARSER_VERSION, CatawikiLotCandidate, CatawikiParseError, canonical_lot_url

BERLIN = ZoneInfo("Europe/Berlin")
PLATFORM = "CATAWIKI"
TOKEN_KEY = "remote_scan_token"
# Interne Zustaende, nicht in der Einstellungsliste sichtbar (Kategorie "internal").
INTERNAL_CATEGORY = "internal"
REQUESTED_KEY = "catawiki_scan_requested_at"
RUNNER_SEEN_KEY = "catawiki_runner_seen_at"
JOB_KEY = "catawiki_scan_job"
# Ausgegeben, noch nicht geliefert: Scan am PC (Aufgabe bis 30 min) plus Luft.
JOB_LEASE = timedelta(minutes=60)
# Geliefert: Warteschlange plus Bewertungs-Task (time_limit 1800 s) plus Luft.
DELIVERED_LEASE = timedelta(minutes=45)
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
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    parser_version: str = Field(max_length=50)
    lots: list[RemoteLot] = Field(default_factory=list, max_length=500)
    errors: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _short_errors(self):
        self.errors = [error[:300] for error in self.errors]
        return self


async def _row(session: AsyncSession, key: str, *, lock: bool = False) -> AppSetting | None:
    statement = select(AppSetting).where(AppSetting.key == key)
    if lock:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def _set_raw(session: AsyncSession, key: str, text: str | None) -> None:
    row = await _row(session, key)
    if row is None:
        session.add(AppSetting(key=key, value=text, is_secret=False, category=INTERNAL_CATEGORY, label=key))
    else:
        row.value = text


def _parse_time(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


async def _get_internal(session: AsyncSession, key: str) -> datetime | None:
    row = await _row(session, key)
    return _parse_time(row.value if row else None)


async def _set_internal(session: AsyncSession, key: str, value: datetime | None) -> None:
    await _set_raw(session, key, value.isoformat() if value else None)


async def _get_job(session: AsyncSession, *, lock: bool = False) -> dict | None:
    row = await _row(session, JOB_KEY, lock=lock)
    try:
        job = json.loads(row.value) if row and row.value else None
    except ValueError:
        return None
    return job if isinstance(job, dict) and job.get("job_id") else None


async def _set_job(session: AsyncSession, job: dict | None) -> None:
    await _set_raw(session, JOB_KEY, json.dumps(job) if job else None)


def _job_active(job: dict | None, now: datetime) -> bool:
    delivered_at = _parse_time((job or {}).get("delivered_at"))
    if delivered_at:
        # Ab der Lieferung zaehlt die Bewertung, nicht mehr der Scan am PC.
        return now - delivered_at < DELIVERED_LEASE
    issued_at = _parse_time((job or {}).get("issued_at"))
    return bool(issued_at and now - issued_at < JOB_LEASE)


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
    job = await _get_job(session, lock=True)

    reason = None
    if _job_active(job, now):
        # Ein Auftrag laeuft noch (am PC oder in der Bewertung): keinen zweiten ausgeben.
        reason = None
    elif urls and requested_at:
        reason = "requested"
    elif urls and scheduled_scan_due(config.get("catawiki_scan_frequency"), state.scanned_at if state else None, now):
        reason = "scheduled"

    job_id = None
    if reason:
        job_id = uuid.uuid4().hex
        await _set_job(session, {
            "job_id": job_id, "reason": reason, "issued_at": now.isoformat(),
            # Nur diese Anforderung erledigt der Auftrag; eine spaetere bleibt stehen.
            "request_at": requested_at.isoformat() if requested_at else None,
            "delivered_at": None,
        })
    await _set_internal(session, RUNNER_SEEN_KEY, now)
    await session.commit()
    try:
        max_results = max(1, min(50, int(config.get("catawiki_max_results_per_url") or 20)))
    except ValueError:
        max_results = 20
    return {
        "run": reason is not None,
        "reason": reason,
        "job_id": job_id,
        "parser_version": PARSER_VERSION,
        # Immer mitliefern: `run` entscheidet, `--force` am Heimrechner braucht die URLs trotzdem.
        "scan_urls": urls,
        "max_results_per_url": max_results,
        "user_agent": config.get("catawiki_user_agent") or None,
        "cookie_header": config.get("catawiki_cookie_header") or None,
    }


async def request_scan(session: AsyncSession, now: datetime | None = None) -> None:
    await _set_internal(session, REQUESTED_KEY, now or datetime.now(UTC))
    await session.commit()


class JobRejectedError(Exception):
    """Ergebnisse passen zu keinem offenen Auftrag (oder zu einer anderen Parser-Version)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def accept_results(session: AsyncSession, data: RemoteScanResults, now: datetime | None = None) -> dict:
    """Ergebnisse genau einmal fuer den ausgegebenen Auftrag annehmen.

    Committet NICHT: der Aufrufer committet erst, wenn der Bewertungs-Task
    eingereiht ist -- sonst stuende der Auftrag als geliefert da, ohne dass
    je bewertet wird (Broker weg), und die Anforderung waere verloren.
    """
    now = now or datetime.now(UTC)
    if data.parser_version != PARSER_VERSION:
        raise JobRejectedError(409, f"Heimrechner-Parser {data.parser_version}, Prod erwartet {PARSER_VERSION}: "
                               "Checkout auf dem PC aktualisieren")
    job = await _get_job(session, lock=True)
    if not job or job["job_id"] != data.job_id or not _job_active(job, now):
        raise JobRejectedError(409, "Kein offener Auftrag mit dieser ID")
    if job.get("delivered_at"):
        raise JobRejectedError(409, "Ergebnisse zu diesem Auftrag sind schon angekommen")
    job["delivered_at"] = now.isoformat()
    await _set_job(session, job)
    requested_at = await _get_internal(session, REQUESTED_KEY)
    if requested_at and job.get("request_at") and requested_at <= _parse_time(job["request_at"]):
        await _set_internal(session, REQUESTED_KEY, None)
    return job


async def finish_job(session: AsyncSession, job_id: str) -> None:
    """Sperre nach der Bewertung freigeben -- nur die eigene."""
    job = await _get_job(session, lock=True)
    if job and job["job_id"] == job_id:
        await _set_job(session, None)
        await session.commit()


async def scan_status(session: AsyncSession) -> dict:
    token = (await session.execute(select(AppSetting).where(AppSetting.key == TOKEN_KEY))).scalar_one_or_none()
    state = (await session.execute(
        select(AuctionScanState).where(AuctionScanState.platform == PLATFORM)
    )).scalar_one_or_none()
    requested_at = await _get_internal(session, REQUESTED_KEY)
    runner_seen_at = await _get_internal(session, RUNNER_SEEN_KEY)
    job = await _get_job(session)
    active = job if _job_active(job, datetime.now(UTC)) else None
    return {
        "job": {
            "reason": active["reason"], "issued_at": active["issued_at"], "delivered_at": active.get("delivered_at"),
        } if active else None,
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
