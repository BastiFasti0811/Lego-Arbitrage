"""Verkaufs-Cockpit: was als Naechstes fotografiert und eingestellt wird.

Der Engpass fuers Ziel 30/49 sind Fotos und das Einstellen, nicht die Texte
(Plan KW 45). Dieses Modul liefert dem Inventar-Kopf die Zahlen dazu:

- Lego-Posten im Bestand ohne Foto (hochgeladene Fotos; ein externer Foto-Link
  zaehlt nicht, eingestellt wird nur mit Foto, siehe ka_posting);
- Entwuerfe bereit zum Einstellen: Posten im Bestand ohne laufende Anzeige mit
  einem DRAFT-Listing mit Preis und mindestens einem Foto (alle Warengruppen);
- Einstell-Auftraege heute (Berliner Kalendertag) gegen das Tageslimit;
- die naechsten Kandidaten fuer "Inserat vorbereiten": Lego mit Foto, ohne
  offenes Listing, hoechster Preisvorschlag zuerst.

Die Regeln fuer "bereit" und "ohne Foto" spiegelt das Frontend in
`frontend/src/pages/listingPrep.js` fuer die Filter; beide muessen gleich zaehlen.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus
from app.models.listing import OPEN_LISTING_STATUSES, ListingStatus
from app.models.listing_posting_job import ListingPostingJob, PostingJobStatus
from app.services.ka_posting import MAX_NEW_ADS_PER_DAY, as_utc, berlin_day_start, pacing_block
from app.services.listing_prepare import draft_price

CANDIDATE_COUNT = 3
_RUNNING = (ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value)


# ── Regeln (rein, ohne DB) ──────────────────────────────────────────


def has_photo(item) -> bool:
    return bool(item.photos)


def is_running(item) -> bool:
    return any(listing.status in _RUNNING for listing in item.listings)


def is_ready_to_post(item) -> bool:
    """Entwurf mit Preis und Foto, noch keine laufende Anzeige."""
    if item.status != InventoryStatus.HOLDING.value or not has_photo(item) or is_running(item):
        return False
    return any(
        listing.status == ListingStatus.DRAFT.value and (listing.current_price or 0) > 0
        for listing in item.listings
    )


def is_candidate(item) -> bool:
    """Lego im Bestand mit Foto und ohne offenes Listing (kein Entwurf, keine Anzeige)."""
    return (
        item.status == InventoryStatus.HOLDING.value
        and item.item_type == InventoryItemType.LEGO.value
        and has_photo(item)
        and not any(listing.status in OPEN_LISTING_STATUSES for listing in item.listings)
    )


@dataclass
class Candidate:
    item_id: int
    set_number: str | None
    set_name: str
    suggested_price: float
    photo_count: int
    storage_location: str | None


def pick_candidates(items, limit: int = CANDIDATE_COUNT) -> list[Candidate]:
    """Hoechster Preisvorschlag zuerst: bei gleicher Arbeit bringt der teurere
    Posten mehr (dieselbe Regel wie das Montags-Paket). Ohne Preisvorschlag
    kein Kandidat, "Inserat vorbereiten" braeuchte sonst einen Preis von Hand."""
    priced = []
    for item in items:
        if not is_candidate(item):
            continue
        price = draft_price(item, None, None)
        if price is not None and price > 0:
            priced.append((price, item))
    priced.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [
        Candidate(
            item_id=item.id,
            set_number=item.set_number,
            set_name=item.set_name,
            suggested_price=round(price, 2),
            photo_count=len(item.photos),
            storage_location=item.storage_location,
        )
        for price, item in priced[:limit]
    ]


@dataclass
class PostingToday:
    posted: int  # heute online gegangen (ohne Testlaeufe)
    started: int  # echte Versuche seit Mitternacht, zaehlen gegen das Tageslimit
    max_per_day: int
    waiting_approval: int  # REQUESTED, egal von wann: wartet auf Sebastian
    queued: int  # APPROVED oder IN_PROGRESS
    failed: int  # heute fehlgeschlagen (ohne Testlaeufe)
    block: str | None  # warum gerade nichts startet (Tageslimit, Abstand)


def count_posting_today(jobs, now: datetime) -> PostingToday:
    """`jobs`: Zeilen mit status, dry_run, started_at, finished_at."""
    day_start = berlin_day_start(now)

    def today(moment) -> bool:
        return moment is not None and as_utc(moment) >= day_start

    real = [job for job in jobs if not job.dry_run]
    starts = [as_utc(job.started_at) for job in real if today(job.started_at)]
    return PostingToday(
        posted=sum(1 for job in real if job.status == PostingJobStatus.POSTED.value and today(job.finished_at)),
        started=len(starts),
        max_per_day=MAX_NEW_ADS_PER_DAY,
        waiting_approval=sum(1 for job in jobs if job.status == PostingJobStatus.REQUESTED.value),
        queued=sum(
            1
            for job in jobs
            if job.status in (PostingJobStatus.APPROVED.value, PostingJobStatus.IN_PROGRESS.value)
        ),
        failed=sum(1 for job in real if job.status == PostingJobStatus.FAILED.value and today(job.finished_at)),
        block=pacing_block(starts, now),
    )


@dataclass
class SalesCockpit:
    lego_holding: int
    lego_without_photo: int
    drafts_ready: int
    posting_today: PostingToday
    candidates: list[Candidate]


def build_cockpit(items, jobs, now: datetime) -> SalesCockpit:
    holding = [item for item in items if item.status == InventoryStatus.HOLDING.value]
    lego = [item for item in holding if item.item_type == InventoryItemType.LEGO.value]
    return SalesCockpit(
        lego_holding=len(lego),
        lego_without_photo=sum(1 for item in lego if not has_photo(item)),
        drafts_ready=sum(1 for item in holding if is_ready_to_post(item)),
        posting_today=count_posting_today(jobs, now),
        candidates=pick_candidates(holding),
    )


# ── Laden ───────────────────────────────────────────────────────────


def posting_jobs_query(since: datetime):
    """Heute angefasste Auftraege plus alle offenen (ein REQUESTED von gestern wartet weiter)."""
    return select(
        ListingPostingJob.status,
        ListingPostingJob.dry_run,
        ListingPostingJob.started_at,
        ListingPostingJob.finished_at,
    ).where(
        (ListingPostingJob.requested_at >= since)
        | (ListingPostingJob.started_at >= since)
        | (ListingPostingJob.finished_at >= since)
        | ListingPostingJob.status.in_(
            (PostingJobStatus.REQUESTED.value, PostingJobStatus.APPROVED.value, PostingJobStatus.IN_PROGRESS.value)
        )
    )


async def load_cockpit(session: AsyncSession, now: datetime | None = None) -> SalesCockpit:
    now = now or datetime.now(UTC)
    items = (
        await session.execute(select(InventoryItem).where(InventoryItem.status == InventoryStatus.HOLDING.value))
    ).scalars().all()
    jobs = (await session.execute(posting_jobs_query(berlin_day_start(now)))).all()
    return build_cockpit(list(items), list(jobs), now)
