"""Kleinanzeigen per Browser einstellen: Auftrag, Freigabe, Leitplanken.

Nachtrag ADR 0002 (Entscheidung Sebastian 25.10.2026). Kleinanzeigen hat keine
API; ein Agent stellt die Anzeige in Sebastians eingeloggtem Chrome auf victus
ein (Skill `kleinanzeigen-einstellen`). Browser-Automation verstoesst gegen die
AGB und kann das Konto kosten. Darum stehen die Grenzen hier im Code und nicht
nur im Skill:

- hoechstens `MAX_NEW_ADS_PER_DAY` echte Einstell-Versuche je Kalendertag
  (Europe/Berlin), mit `MIN_GAP_BETWEEN_ADS` Abstand und immer nur einer zur
  Zeit;
- die ersten `APPROVAL_FREE_AFTER` Inserate brauchen Sebastians Freigabe je
  Anzeige. Danach nur ohne Freigabe, wenn er die Einstellung
  `ka_autopost_without_approval` selbst einschaltet;
- ein Testlauf (`dry_run`) fuellt das Formular und veroeffentlicht nicht;
- Loeschen gibt es nicht, auch nicht als Aktion.

Keine Zugangsdaten, kein Login: Ist die Sitzung abgelaufen, bricht der Agent ab
und meldet es.

Der Service committet nicht; Route und CLI entscheiden ueber die Transaktion.
"""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from PIL import Image, ImageOps
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.condition import normalize_condition
from app.models.inventory import InventoryItem, InventoryItemType
from app.models.listing import Listing, ListingPlatform, ListingStatus, PriceType
from app.models.listing_posting_job import (
    OPEN_POSTING_STATUSES,
    ApprovalSource,
    ListingPostingJob,
    PostingAction,
    PostingJobStatus,
)
from app.models.settings import AppSetting
from app.runtime_settings import as_bool
from app.services.listing_prepare import ListingPrepareError, activate_draft, check_item_can_be_listed

logger = structlog.get_logger()

BERLIN = ZoneInfo("Europe/Berlin")

MAX_NEW_ADS_PER_DAY = 3
MIN_GAP_BETWEEN_ADS = timedelta(minutes=20)
APPROVAL_FREE_AFTER = 3
# Ein Auftrag, der so lange IN_PROGRESS haengt, ist abgebrochen worden (Agent
# beendet, Rechner aus). Er wird FAILED und blockiert nicht laenger, zaehlt
# aber weiter fuers Tageslimit.
STALE_IN_PROGRESS = timedelta(hours=2)

SETTING_AUTOPOST = "ka_autopost_without_approval"
STALE_ERROR = "Ohne Rückmeldung abgebrochen — in „Meine Anzeigen“ prüfen, ob die Anzeige online ist"

# Grenzen des Kleinanzeigen-Formulars.
KA_TITLE_MAX = 65
KA_BODY_MAX = 4000
# Die Browser-Steuerung laedt hoechstens zehn Dateien je Aufruf hoch.
KA_MAX_PHOTOS = 10
PHOTO_MAX_DIMENSION = 1600
PHOTO_JPEG_QUALITY = 85

# Auswahlwerte im Feld "Zustand" auf kleinanzeigen.de (Gegenrichtung zu
# `_LABEL_TO_CONDITION` in app.domain.condition).
_KA_CONDITION = {
    "NEW_SEALED": "Neu",
    "NEW_OPEN_BOX": "Neu",
    "USED_COMPLETE": "Gut",
    "USED_INCOMPLETE": "In Ordnung",
    "UNKNOWN": None,
}

# Kategoriepfad, den der Agent im Formular waehlt. Bei Lego eindeutig; bei
# sonstigen Artikeln uebernimmt er den Vorschlag von Kleinanzeigen, sofern er
# passt, und bricht sonst ab.
KA_LEGO_CATEGORY = "Familie, Kind & Baby > Spielzeug"
KA_LEGO_TYPE = "LEGO & Duplo"


class PostingError(Exception):
    """Fachlicher Grund, warum ein Auftrag nicht angelegt oder weitergeschaltet wird."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


# ── reine Regeln ────────────────────────────────────────────────────


def ka_title(title: str) -> str:
    """Titel auf die 65 Zeichen von Kleinanzeigen kuerzen, an einer Wortgrenze."""
    title = " ".join((title or "").split())
    if len(title) <= KA_TITLE_MAX:
        return title
    cut = title[:KA_TITLE_MAX]
    space = cut.rfind(" ")
    # Nur an der Wortgrenze kuerzen, wenn dabei nicht der halbe Titel verloren geht.
    if space >= KA_TITLE_MAX // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-")


def ka_condition(condition: str | None) -> str | None:
    return _KA_CONDITION[normalize_condition(condition)]


def ka_price(price: float) -> int:
    """Kleinanzeigen nimmt nur ganze Euro. Kaufmaennisch gerundet, mindestens 1."""
    return max(1, math.floor(price + 0.5))


def ka_price_type(price_type: str) -> str:
    return "VB" if price_type == PriceType.VB.value else "Festpreis"


def check_listing_postable(listing: Listing, item: InventoryItem) -> None:
    """Was ein Entwurf haben muss, bevor ein Agent ihn einstellen darf."""
    if listing.platform != ListingPlatform.KLEINANZEIGEN.value:
        raise PostingError("Automatisch eingestellt wird nur auf Kleinanzeigen")
    if listing.status != ListingStatus.DRAFT.value:
        raise PostingError("Nur ein Entwurf lässt sich einstellen")
    try:
        check_item_can_be_listed(item)
    except ListingPrepareError as exc:
        raise PostingError(exc.detail) from exc
    if not (listing.title or "").strip() or not (listing.body or "").strip():
        raise PostingError("Der Entwurf hat noch keinen Titel oder Text")
    if not listing.current_price or listing.current_price <= 0:
        raise PostingError("Der Entwurf hat keinen Preis")
    if len(listing.body) > KA_BODY_MAX:
        raise PostingError(f"Der Text ist länger als {KA_BODY_MAX} Zeichen")
    if not item.photos:
        raise PostingError("Ohne Foto wird nicht eingestellt")
    if ka_condition(item.condition) is None:
        raise PostingError("Zustand fehlt — bitte am Posten eintragen")


def needs_approval(*, dry_run: bool, autopost_enabled: bool, posted_count: int) -> bool:
    """Freigabe je Anzeige, bis Sebastian sie nach den ersten drei abschaltet."""
    if dry_run:
        return False
    return not (autopost_enabled and posted_count >= APPROVAL_FREE_AFTER)


def as_utc(value: datetime) -> datetime:
    """Zeitstempel aus der DB vergleichbar machen (SQLite liefert sie ohne Zone)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def berlin_day_start(now: datetime) -> datetime:
    """Mitternacht in Berlin, als UTC-Zeitpunkt."""
    local = now.astimezone(BERLIN)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def pacing_block(started_today: list[datetime], now: datetime) -> str | None:
    """Grund, warum gerade kein echter Auftrag starten darf, sonst None.

    `started_today` sind die Startzeiten aller echten Versuche seit Mitternacht
    (Berlin), auch der fehlgeschlagenen: jeder Versuch ist Aktivitaet auf der
    Seite."""
    if len(started_today) >= MAX_NEW_ADS_PER_DAY:
        return f"Tageslimit erreicht ({MAX_NEW_ADS_PER_DAY} Inserate) — morgen weiter"
    if started_today:
        next_allowed = max(as_utc(t) for t in started_today) + MIN_GAP_BETWEEN_ADS
        if now < next_allowed:
            local = next_allowed.astimezone(BERLIN)
            return f"Pause zwischen zwei Inseraten — frühestens {local:%H:%M} Uhr"
    return None


# ── Zustandswechsel ─────────────────────────────────────────────────


def _require_status(job: ListingPostingJob, *allowed: PostingJobStatus) -> None:
    if job.status not in {s.value for s in allowed}:
        raise PostingError(f"Auftrag {job.id} steht auf {job.status}")


def approve(job: ListingPostingJob, now: datetime) -> None:
    _require_status(job, PostingJobStatus.REQUESTED)
    job.status = PostingJobStatus.APPROVED.value
    job.approved_by = ApprovalSource.SEBASTIAN.value
    job.approved_at = now


def cancel(job: ListingPostingJob, now: datetime, reason: str | None = None) -> None:
    """Nur vor dem Start: ein laufender Auftrag endet ueber `mark_failed`."""
    _require_status(job, PostingJobStatus.REQUESTED, PostingJobStatus.APPROVED)
    job.status = PostingJobStatus.CANCELLED.value
    job.finished_at = now
    job.error = reason


def mark_started(job: ListingPostingJob, now: datetime) -> None:
    _require_status(job, PostingJobStatus.APPROVED)
    if not job.dry_run and job.approved_by is None:
        raise PostingError(f"Auftrag {job.id} ist nicht freigegeben")
    job.status = PostingJobStatus.IN_PROGRESS.value
    job.started_at = now


def mark_posted(job: ListingPostingJob, item: InventoryItem, url: str, now: datetime) -> None:
    """Anzeige ist online: Auftrag schliessen, Listing aktiv schalten."""
    _require_status(job, PostingJobStatus.IN_PROGRESS)
    if job.dry_run:
        raise PostingError("Ein Testlauf veröffentlicht nicht — `draft-saved` statt `done`")
    try:
        activate_draft(job.listing, item, url=url)
    except ListingPrepareError as exc:
        raise PostingError(exc.detail) from exc
    job.status = PostingJobStatus.POSTED.value
    job.result_url = job.listing.url
    job.finished_at = now


def mark_draft_saved(job: ListingPostingJob, now: datetime, note: str | None = None) -> None:
    """Formular gefuellt, nicht veroeffentlicht. Das Listing bleibt Entwurf."""
    _require_status(job, PostingJobStatus.IN_PROGRESS)
    job.status = PostingJobStatus.DRAFT_SAVED.value
    job.finished_at = now
    job.error = note


def mark_failed(job: ListingPostingJob, now: datetime, error: str) -> None:
    _require_status(job, PostingJobStatus.IN_PROGRESS)
    job.status = PostingJobStatus.FAILED.value
    job.finished_at = now
    job.error = (error or "").strip() or "ohne Angabe"


# ── Datenbank ───────────────────────────────────────────────────────


def _job_query():
    return select(ListingPostingJob).options(selectinload(ListingPostingJob.listing).selectinload(Listing.item))


async def get_job(session: AsyncSession, job_id: int) -> ListingPostingJob:
    result = await session.execute(_job_query().where(ListingPostingJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise PostingError(f"Auftrag {job_id} gibt es nicht")
    return job


async def autopost_enabled(session: AsyncSession) -> bool:
    result = await session.execute(select(AppSetting.value).where(AppSetting.key == SETTING_AUTOPOST))
    return as_bool(result.scalar_one_or_none())


async def posted_count(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ListingPostingJob)
        .where(
            ListingPostingJob.status == PostingJobStatus.POSTED.value,
            ListingPostingJob.dry_run.is_(False),
        )
    )
    return int(result.scalar_one())


async def real_starts_today(session: AsyncSession, now: datetime) -> list[datetime]:
    result = await session.execute(
        select(ListingPostingJob.started_at).where(
            ListingPostingJob.dry_run.is_(False),
            ListingPostingJob.started_at.is_not(None),
            ListingPostingJob.started_at >= berlin_day_start(now),
        )
    )
    return [as_utc(row) for row in result.scalars()]


async def open_job_for(session: AsyncSession, listing_id: int) -> ListingPostingJob | None:
    result = await session.execute(
        _job_query().where(
            ListingPostingJob.listing_id == listing_id,
            ListingPostingJob.status.in_(OPEN_POSTING_STATUSES),
        )
    )
    return result.scalar_one_or_none()


async def request_posting(
    session: AsyncSession, listing: Listing, item: InventoryItem, *, dry_run: bool, now: datetime
) -> ListingPostingJob:
    """Auftrag anlegen. Ohne Freigabe startet nur ein Testlauf oder, nach den
    ersten drei Inseraten, ein Auftrag bei eingeschaltetem Autopost."""
    check_listing_postable(listing, item)
    if await open_job_for(session, listing.id) is not None:
        raise PostingError("Für diesen Entwurf läuft schon ein Einstell-Auftrag")

    approval = needs_approval(
        dry_run=dry_run,
        autopost_enabled=await autopost_enabled(session),
        posted_count=await posted_count(session),
    )
    job = ListingPostingJob(
        listing_id=listing.id,
        action=PostingAction.CREATE.value,
        dry_run=dry_run,
        requested_at=now,
    )
    job.listing = listing
    if approval:
        job.status = PostingJobStatus.REQUESTED.value
    else:
        job.status = PostingJobStatus.APPROVED.value
        job.approved_by = (ApprovalSource.TESTLAUF if dry_run else ApprovalSource.AUTO).value
        job.approved_at = now
    session.add(job)
    logger.info(
        "ka_posting.requested",
        listing_id=listing.id,
        item_id=item.id,
        dry_run=dry_run,
        status=job.status,
    )
    return job


@dataclass
class ClaimResult:
    job: ListingPostingJob | None
    reason: str | None = None
    cancelled: list[int] = field(default_factory=list)


async def claim_next(session: AsyncSession, now: datetime) -> ClaimResult:
    """Den aeltesten freigegebenen Auftrag holen und auf IN_PROGRESS setzen.

    Prueft vorher die Leitplanken. Auftraege, deren Entwurf inzwischen weg ist
    (von Hand eingestellt, verworfen, Posten verkauft), werden abgebrochen."""
    running = await session.execute(
        select(ListingPostingJob).where(ListingPostingJob.status == PostingJobStatus.IN_PROGRESS.value)
    )
    for job in running.scalars():
        if job.started_at is not None and now - as_utc(job.started_at) < STALE_IN_PROGRESS:
            return ClaimResult(None, f"Auftrag {job.id} läuft noch — erst abschließen (`done`/`draft-saved`/`fail`)")
        # Haengengeblieben. Ob die Anzeige trotzdem online ging, weiss hier
        # keiner -- darum FAILED mit Pruefhinweis, nie stillschweigend neu.
        mark_failed(job, now, STALE_ERROR)
        logger.warning("ka_posting.stale", job_id=job.id, listing_id=job.listing_id)

    result = await session.execute(
        _job_query()
        .where(ListingPostingJob.status == PostingJobStatus.APPROVED.value)
        .order_by(ListingPostingJob.approved_at, ListingPostingJob.id)
    )
    cancelled: list[int] = []
    starts = await real_starts_today(session, now)
    for job in result.scalars():
        listing = job.listing
        try:
            check_listing_postable(listing, listing.item)
        except PostingError as exc:
            cancel(job, now, reason=f"Abgebrochen vor dem Start: {exc.detail}")
            cancelled.append(job.id)
            continue
        if not job.dry_run:
            block = pacing_block(starts, now)
            if block:
                # Testlaeufe duerfen trotzdem, sie erzeugen keine Anzeige.
                continue
        mark_started(job, now)
        logger.info("ka_posting.started", job_id=job.id, listing_id=job.listing_id, dry_run=job.dry_run)
        return ClaimResult(job, None, cancelled)

    block = pacing_block(starts, now)
    has_waiting = await session.execute(
        select(func.count())
        .select_from(ListingPostingJob)
        .where(ListingPostingJob.status == PostingJobStatus.APPROVED.value)
    )
    if int(has_waiting.scalar_one()) and block:
        return ClaimResult(None, block, cancelled)
    return ClaimResult(None, "Kein freigegebener Auftrag", cancelled)


# ── Daten fuer den Agenten ──────────────────────────────────────────


def ordered_photos(item: InventoryItem) -> list:
    return sorted(item.photos, key=lambda p: (p.sort_order or 0, p.id or 0))


def export_photos(item: InventoryItem, storage_root: Path, target_dir: Path) -> list[Path]:
    """Fotos in Anzeigen-Reihenfolge als 01.jpg, 02.jpg ... ablegen.

    Verkleinert und ohne Metadaten: in Handyfotos steckt der Aufnahmeort, und
    der gehoert nicht in eine oeffentliche Anzeige."""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)
    written: list[Path] = []
    for photo in ordered_photos(item):
        if len(written) >= KA_MAX_PHOTOS:
            break
        source = storage_root / str(item.id) / photo.filename
        if not source.exists():
            logger.warning("ka_posting.photo_missing", item_id=item.id, photo=photo.filename)
            continue
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((PHOTO_MAX_DIMENSION, PHOTO_MAX_DIMENSION))
            target = target_dir / f"{len(written) + 1:02d}.jpg"
            img.save(target, "JPEG", quality=PHOTO_JPEG_QUALITY, optimize=True)
        written.append(target)
    return written


def build_payload(job: ListingPostingJob, item: InventoryItem, photo_paths: list[Path]) -> dict:
    """Alles, was der Agent ins Formular schreibt. Titel und Preis schon in der
    Form, die Kleinanzeigen annimmt."""
    listing = job.listing
    is_lego = item.item_type == InventoryItemType.LEGO.value
    return {
        "job_id": job.id,
        "dry_run": job.dry_run,
        "listing_id": listing.id,
        "item_id": item.id,
        "title": ka_title(listing.title or ""),
        "body": listing.body,
        "price": ka_price(listing.current_price),
        "price_type": ka_price_type(listing.price_type),
        "condition": ka_condition(item.condition),
        "category": KA_LEGO_CATEGORY if is_lego else (listing.platform_category or None),
        "category_type": KA_LEGO_TYPE if is_lego else None,
        "shipping": "Versand möglich",
        "photos": [str(path) for path in photo_paths],
    }


@dataclass
class GuardrailStatus:
    posted_count: int
    autopost_enabled: bool
    approval_required: bool
    starts_today: int
    max_per_day: int
    block: str | None


async def guardrail_status(session: AsyncSession, now: datetime) -> GuardrailStatus:
    """Was die App ueber den Einstell-Agenten anzeigt: Freigabepflicht und Tageslimit."""
    count = await posted_count(session)
    enabled = await autopost_enabled(session)
    starts = await real_starts_today(session, now)
    return GuardrailStatus(
        posted_count=count,
        autopost_enabled=enabled,
        approval_required=needs_approval(dry_run=False, autopost_enabled=enabled, posted_count=count),
        starts_today=len(starts),
        max_per_day=MAX_NEW_ADS_PER_DAY,
        block=pacing_block(starts, now),
    )
