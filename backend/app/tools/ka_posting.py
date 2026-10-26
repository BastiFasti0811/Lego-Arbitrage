"""Schnittstelle fuer den Einstell-Agenten (Skill `kleinanzeigen-einstellen`).

Laeuft im API-Container auf victus. Der Agent holt sich hier den naechsten
freigegebenen Auftrag, stellt die Anzeige im Browser ein und meldet das
Ergebnis zurueck. Freigeben kann nur Sebastian in der App; hier gibt es dafuer
keinen Befehl.

    python -m app.tools.ka_posting status
    python -m app.tools.ka_posting request <listing_id> [--dry-run]
    python -m app.tools.ka_posting next
    python -m app.tools.ka_posting done <job_id> --url https://www.kleinanzeigen.de/s-anzeige/...
    python -m app.tools.ka_posting draft-saved <job_id> [--note TEXT]
    python -m app.tools.ka_posting fail <job_id> --error TEXT

Jede Ausgabe ist ein JSON-Objekt auf stdout; Exit-Code 1 bei fachlichem Fehler.
"""

import argparse
import asyncio
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.models.base import async_session
from app.models.inventory import InventoryItem
from app.models.listing import Listing
from app.models.listing_posting_job import OPEN_POSTING_STATUSES, ListingPostingJob
from app.services import ka_posting
from app.services.ka_posting import PostingError

PHOTO_STORAGE_ROOT = Path(settings.media_root) / "inventory_photos"
EXPORT_ROOT = Path(settings.media_root) / "ka_export"


def export_dir(job_id: int, root: Path = EXPORT_ROOT) -> Path:
    return root / f"job_{job_id}"


def _job_dict(job: ListingPostingJob) -> dict:
    return {
        "job_id": job.id,
        "listing_id": job.listing_id,
        "status": job.status,
        "dry_run": job.dry_run,
        "approved_by": job.approved_by,
        "result_url": job.result_url,
        "error": job.error,
    }


async def cmd_status(session, now: datetime) -> dict:
    status = await ka_posting.guardrail_status(session, now)
    result = await session.execute(
        select(ListingPostingJob)
        .where(ListingPostingJob.status.in_(OPEN_POSTING_STATUSES))
        .order_by(ListingPostingJob.requested_at)
    )
    return {
        "posted_count": status.posted_count,
        "autopost_enabled": status.autopost_enabled,
        "approval_required": status.approval_required,
        "starts_today": status.starts_today,
        "max_per_day": status.max_per_day,
        "block": status.block,
        "open_jobs": [_job_dict(job) for job in result.scalars()],
    }


async def cmd_request(session, now: datetime, listing_id: int, dry_run: bool) -> dict:
    result = await session.execute(select(Listing).where(Listing.id == listing_id))
    listing = result.scalar_one_or_none()
    if listing is None:
        raise PostingError(f"Listing {listing_id} gibt es nicht")
    result = await session.execute(select(InventoryItem).where(InventoryItem.id == listing.item_id))
    item = result.scalar_one()
    job = await ka_posting.request_posting(session, listing, item, dry_run=dry_run, now=now)
    await session.commit()
    await session.refresh(job)
    return _job_dict(job)


async def cmd_next(session, now: datetime, storage_root: Path, export_root: Path) -> dict:
    claim = await ka_posting.claim_next(session, now)
    if claim.job is None:
        await session.commit()  # abgebrochene oder haengengebliebene Auftraege festhalten
        return {"job": None, "reason": claim.reason, "cancelled": claim.cancelled}
    job = claim.job
    item = job.listing.item
    photos = ka_posting.export_photos(item, storage_root, export_dir(job.id, export_root))
    if not photos:
        ka_posting.mark_failed(job, now, "Keine Fotodatei gefunden")
        await session.commit()
        raise PostingError(f"Auftrag {job.id}: keine Fotodatei gefunden")
    await session.commit()
    return {"job": ka_posting.build_payload(job, item, photos), "cancelled": claim.cancelled}


def _cleanup(job_id: int, export_root: Path) -> None:
    shutil.rmtree(export_dir(job_id, export_root), ignore_errors=True)


async def cmd_done(session, now: datetime, job_id: int, url: str, export_root: Path) -> dict:
    job = await ka_posting.get_job(session, job_id)
    ka_posting.mark_posted(job, job.listing.item, url, now)
    await session.commit()
    _cleanup(job_id, export_root)
    return _job_dict(job)


async def cmd_draft_saved(session, now: datetime, job_id: int, note: str | None, export_root: Path) -> dict:
    job = await ka_posting.get_job(session, job_id)
    ka_posting.mark_draft_saved(job, now, note)
    await session.commit()
    _cleanup(job_id, export_root)
    return _job_dict(job)


async def cmd_fail(session, now: datetime, job_id: int, error: str, export_root: Path) -> dict:
    job = await ka_posting.get_job(session, job_id)
    ka_posting.mark_failed(job, now, error)
    await session.commit()
    _cleanup(job_id, export_root)
    return _job_dict(job)


async def run(args: argparse.Namespace) -> dict:
    now = datetime.now(UTC)
    async with async_session() as session:
        if args.command == "status":
            return await cmd_status(session, now)
        if args.command == "request":
            return await cmd_request(session, now, args.listing_id, args.dry_run)
        if args.command == "next":
            return await cmd_next(session, now, PHOTO_STORAGE_ROOT, EXPORT_ROOT)
        if args.command == "done":
            return await cmd_done(session, now, args.job_id, args.url, EXPORT_ROOT)
        if args.command == "draft-saved":
            return await cmd_draft_saved(session, now, args.job_id, args.note, EXPORT_ROOT)
        if args.command == "fail":
            return await cmd_fail(session, now, args.job_id, args.error, EXPORT_ROOT)
    raise PostingError(f"Unbekannter Befehl: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.tools.ka_posting")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    request = sub.add_parser("request")
    request.add_argument("listing_id", type=int)
    request.add_argument("--dry-run", action="store_true")
    sub.add_parser("next")
    done = sub.add_parser("done")
    done.add_argument("job_id", type=int)
    done.add_argument("--url", required=True)
    saved = sub.add_parser("draft-saved")
    saved.add_argument("job_id", type=int)
    saved.add_argument("--note")
    fail = sub.add_parser("fail")
    fail.add_argument("job_id", type=int)
    fail.add_argument("--error", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = asyncio.run(run(args))
    except PostingError as exc:
        print(json.dumps({"error": exc.detail}, ensure_ascii=False))
        return 1
    print(json.dumps(output, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
