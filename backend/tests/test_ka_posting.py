"""Kleinanzeigen per Browser einstellen: Leitplanken, Ablauf, Fotos, CLI."""

from datetime import UTC, date, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import AppSetting, Base
from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus
from app.models.inventory_photo import InventoryPhoto
from app.models.listing import Listing, ListingStatus
from app.models.listing_posting_job import ListingPostingJob, PostingJobStatus
from app.services import ka_posting
from app.services.ka_posting import (
    KA_MAX_PHOTOS,
    KA_TITLE_MAX,
    PostingError,
    berlin_day_start,
    ka_condition,
    ka_price,
    ka_price_type,
    ka_title,
    needs_approval,
    pacing_block,
)
from app.tools import ka_posting as cli

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
KA_URL = "https://www.kleinanzeigen.de/s-anzeige/lego-75192/1234567890-23-1234"


# ── reine Regeln ─────────────────────────────────────────────────────


def test_title_fits_kleinanzeigen_limit_and_cuts_at_word_boundary():
    long_title = "LEGO 75192 Star Wars Millennium Falcon Ultimate Collector Series NEU OVP versiegelt"
    title = ka_title(long_title)
    assert len(title) <= KA_TITLE_MAX
    assert long_title.startswith(title)
    assert not title.endswith(" ")
    assert long_title[len(title)] == " "  # an der Wortgrenze, kein halbes Wort


def test_short_title_stays_and_whitespace_collapses():
    assert ka_title("  LEGO 10305   Burg  ") == "LEGO 10305 Burg"


def test_price_is_whole_euros_rounded_half_up():
    assert ka_price(49.5) == 50
    assert ka_price(49.49) == 49
    assert ka_price(0.2) == 1


def test_price_type_and_condition_use_kleinanzeigen_labels():
    assert ka_price_type("VB") == "VB"
    assert ka_price_type("FIXED") == "Festpreis"
    assert ka_condition("NEW_SEALED") == "Neu"
    assert ka_condition("USED_COMPLETE") == "Gut"
    assert ka_condition("USED_INCOMPLETE") == "In Ordnung"
    assert ka_condition(None) is None


@pytest.mark.parametrize(
    ("dry_run", "autopost", "posted", "expected"),
    [
        (False, False, 0, True),
        (False, False, 10, True),  # ohne Sebastians Schalter immer Freigabe
        (False, True, 2, True),  # die ersten drei immer mit Freigabe
        (False, True, 3, False),
        (True, False, 0, False),  # Testlauf: nichts geht online
    ],
)
def test_approval_needed_until_sebastian_switches_it_off_after_three(dry_run, autopost, posted, expected):
    assert needs_approval(dry_run=dry_run, autopost_enabled=autopost, posted_count=posted) is expected


def test_pacing_blocks_fourth_ad_of_the_day():
    starts = [NOW - timedelta(hours=h) for h in (1, 2, 3)]
    assert "Tageslimit" in pacing_block(starts, NOW)


def test_pacing_requires_gap_between_ads():
    assert "Pause" in pacing_block([NOW - timedelta(minutes=10)], NOW)
    assert pacing_block([NOW - timedelta(minutes=25)], NOW) is None
    assert pacing_block([], NOW) is None


def test_pacing_accepts_naive_timestamps_from_sqlite():
    naive = (NOW - timedelta(minutes=5)).replace(tzinfo=None)
    assert "Pause" in pacing_block([naive], NOW)


def test_day_starts_at_berlin_midnight():
    # 23:30 UTC am 26.10. ist in Berlin (Winterzeit) schon der 27.10.
    late = datetime(2026, 10, 26, 23, 30, tzinfo=UTC)
    assert berlin_day_start(late) == datetime(2026, 10, 26, 23, 0, tzinfo=UTC)


# ── Echte Zeilen: In-Memory-SQLite ────────────────────────────────────


class _AsyncSessionAdapter:
    def __init__(self, sync_session):
        self._session = sync_session

    async def execute(self, statement):
        return self._session.execute(statement)

    def add(self, obj):
        self._session.add(obj)

    async def commit(self):
        self._session.commit()

    async def rollback(self):
        self._session.rollback()

    async def refresh(self, obj):
        self._session.refresh(obj)

    async def delete(self, obj):
        self._session.delete(obj)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as sync_session:
        yield _AsyncSessionAdapter(sync_session)
    engine.dispose()


def _draft(db, *, photos=1, platform="KLEINANZEIGEN", condition="NEW_SEALED", set_number="75192"):
    item = InventoryItem(
        item_type=InventoryItemType.LEGO.value,
        set_number=set_number,
        set_name="Millennium Falcon",
        buy_price=100.0,
        buy_shipping=0.0,
        buy_date=date(2026, 1, 10),
        condition=condition,
        quantity=1,
        status=InventoryStatus.HOLDING.value,
    )
    db.add(item)
    db._session.flush()
    for index in range(photos):
        db.add(InventoryPhoto(item_id=item.id, filename=f"p{index}.jpg", sort_order=index))
    listing = Listing(
        item_id=item.id,
        platform=platform,
        status=ListingStatus.DRAFT.value,
        price_type="VB",
        title="LEGO 75192 Millennium Falcon NEU OVP",
        body="LEGO Set 75192\nZustand: Neu & Originalverpackt (OVP)\n\nVersand möglich.",
        current_price=649.9,
        check_interval_days=14,
        price_drop_percent=10.0,
    )
    db.add(listing)
    db._session.flush()
    db._session.refresh(item)
    return listing, item


def _posted_job(db, listing, *, started_at):
    job = ListingPostingJob(
        listing_id=listing.id,
        action="CREATE",
        status=PostingJobStatus.POSTED.value,
        dry_run=False,
        approved_by="SEBASTIAN",
        requested_at=started_at,
        started_at=started_at,
        finished_at=started_at,
    )
    db.add(job)
    db._session.flush()
    return job


async def test_real_request_waits_for_approval_and_is_not_claimed(db):
    listing, item = _draft(db)
    job = await ka_posting.request_posting(db, listing, item, dry_run=False, now=NOW)
    await db.commit()
    assert job.status == PostingJobStatus.REQUESTED.value
    assert job.approved_by is None

    claim = await ka_posting.claim_next(db, NOW)
    assert claim.job is None
    assert job.status == PostingJobStatus.REQUESTED.value


async def test_full_flow_approve_claim_post_activates_listing(db):
    listing, item = _draft(db)
    job = await ka_posting.request_posting(db, listing, item, dry_run=False, now=NOW)
    await db.commit()
    ka_posting.approve(job, NOW)
    await db.commit()

    claim = await ka_posting.claim_next(db, NOW + timedelta(minutes=1))
    assert claim.job is job
    assert job.status == PostingJobStatus.IN_PROGRESS.value

    ka_posting.mark_posted(job, item, KA_URL, NOW + timedelta(minutes=5))
    await db.commit()
    assert job.status == PostingJobStatus.POSTED.value
    assert job.result_url == KA_URL
    assert listing.status == ListingStatus.ACTIVE.value
    assert listing.url == KA_URL
    assert listing.min_price == pytest.approx(454.93)


async def test_second_request_for_same_draft_is_rejected(db):
    listing, item = _draft(db)
    await ka_posting.request_posting(db, listing, item, dry_run=True, now=NOW)
    await db.commit()
    with pytest.raises(PostingError, match="schon ein Einstell-Auftrag"):
        await ka_posting.request_posting(db, listing, item, dry_run=False, now=NOW)


async def test_dry_run_needs_no_approval_and_never_publishes(db):
    listing, item = _draft(db)
    job = await ka_posting.request_posting(db, listing, item, dry_run=True, now=NOW)
    await db.commit()
    assert job.status == PostingJobStatus.APPROVED.value
    assert job.approved_by == "TESTLAUF"

    claim = await ka_posting.claim_next(db, NOW)
    assert claim.job is job
    with pytest.raises(PostingError, match="Testlauf"):
        ka_posting.mark_posted(job, item, KA_URL, NOW)

    ka_posting.mark_draft_saved(job, NOW, "Formular gefüllt, nicht veröffentlicht")
    await db.commit()
    assert job.status == PostingJobStatus.DRAFT_SAVED.value
    assert listing.status == ListingStatus.DRAFT.value
    assert listing.url is None


async def test_autopost_switch_only_counts_after_three_posted_ads(db):
    db.add(AppSetting(key="ka_autopost_without_approval", value="true", category="kleinanzeigen"))
    previous = []
    for index in range(2):
        old_listing, _ = _draft(db, set_number=f"1000{index}")
        previous.append(_posted_job(db, old_listing, started_at=NOW - timedelta(days=3 + index)))

    listing, item = _draft(db)
    job = await ka_posting.request_posting(db, listing, item, dry_run=False, now=NOW)
    assert job.status == PostingJobStatus.REQUESTED.value  # erst zwei eingestellt

    third_listing, _ = _draft(db, set_number="10002")
    _posted_job(db, third_listing, started_at=NOW - timedelta(days=1))
    fourth_listing, fourth_item = _draft(db, set_number="10003")
    auto_job = await ka_posting.request_posting(db, fourth_listing, fourth_item, dry_run=False, now=NOW)
    assert auto_job.status == PostingJobStatus.APPROVED.value
    assert auto_job.approved_by == "AUTO"


async def test_claim_respects_daily_limit_but_lets_dry_run_through(db):
    for index in range(3):
        old_listing, _ = _draft(db, set_number=f"2000{index}")
        _posted_job(db, old_listing, started_at=NOW - timedelta(hours=index + 1))

    listing, item = _draft(db)
    real = await ka_posting.request_posting(db, listing, item, dry_run=False, now=NOW)
    ka_posting.approve(real, NOW)
    await db.commit()

    claim = await ka_posting.claim_next(db, NOW)
    assert claim.job is None
    assert "Tageslimit" in claim.reason
    assert real.status == PostingJobStatus.APPROVED.value

    test_listing, test_item = _draft(db, set_number="30000")
    dry = await ka_posting.request_posting(db, test_listing, test_item, dry_run=True, now=NOW)
    await db.commit()
    claim = await ka_posting.claim_next(db, NOW)
    assert claim.job is dry


async def test_yesterdays_ads_do_not_count_for_today(db):
    for index in range(3):
        old_listing, _ = _draft(db, set_number=f"4000{index}")
        _posted_job(db, old_listing, started_at=datetime(2026, 10, 25, 20, index, tzinfo=UTC))

    listing, item = _draft(db)
    job = await ka_posting.request_posting(db, listing, item, dry_run=False, now=NOW)
    ka_posting.approve(job, NOW)
    await db.commit()
    claim = await ka_posting.claim_next(db, NOW)
    assert claim.job is job


async def test_only_one_job_runs_at_a_time(db):
    first_listing, first_item = _draft(db)
    first = await ka_posting.request_posting(db, first_listing, first_item, dry_run=True, now=NOW)
    second_listing, second_item = _draft(db, set_number="50000")
    await ka_posting.request_posting(db, second_listing, second_item, dry_run=True, now=NOW)
    await db.commit()

    assert (await ka_posting.claim_next(db, NOW)).job is first
    claim = await ka_posting.claim_next(db, NOW + timedelta(minutes=1))
    assert claim.job is None
    assert "läuft noch" in claim.reason


async def test_stale_in_progress_job_fails_with_check_hint(db):
    listing, item = _draft(db)
    job = await ka_posting.request_posting(db, listing, item, dry_run=True, now=NOW)
    await db.commit()
    await ka_posting.claim_next(db, NOW)
    await db.commit()

    claim = await ka_posting.claim_next(db, NOW + timedelta(hours=3))
    assert job.status == PostingJobStatus.FAILED.value
    assert "Meine Anzeigen" in job.error
    assert claim.job is None


async def test_claim_cancels_job_whose_draft_was_posted_by_hand(db):
    listing, item = _draft(db)
    job = await ka_posting.request_posting(db, listing, item, dry_run=False, now=NOW)
    ka_posting.approve(job, NOW)
    await db.commit()
    listing.status = ListingStatus.ACTIVE.value  # Sebastian war schneller
    await db.commit()

    claim = await ka_posting.claim_next(db, NOW)
    assert claim.job is None
    assert claim.cancelled == [job.id]
    assert job.status == PostingJobStatus.CANCELLED.value


async def test_cancel_only_before_start_and_fail_only_while_running(db):
    listing, item = _draft(db)
    job = await ka_posting.request_posting(db, listing, item, dry_run=True, now=NOW)
    await ka_posting.claim_next(db, NOW)
    with pytest.raises(PostingError):
        ka_posting.cancel(job, NOW)
    ka_posting.mark_failed(job, NOW, "Login-Seite statt Formular")
    assert job.status == PostingJobStatus.FAILED.value
    assert job.error == "Login-Seite statt Formular"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"platform": "EBAY"}, "nur auf Kleinanzeigen"),
        ({"photos": 0}, "Ohne Foto"),
        ({"condition": "UNKNOWN"}, "Zustand fehlt"),
    ],
)
async def test_draft_must_be_complete_before_requesting(db, kwargs, message):
    listing, item = _draft(db, **kwargs)
    with pytest.raises(PostingError, match=message):
        await ka_posting.request_posting(db, listing, item, dry_run=True, now=NOW)


async def test_sold_item_cannot_be_requested(db):
    listing, item = _draft(db)
    item.status = InventoryStatus.SOLD.value
    with pytest.raises(PostingError, match="Verkaufte"):
        await ka_posting.request_posting(db, listing, item, dry_run=True, now=NOW)


# ── Fotos ────────────────────────────────────────────────────────────


def _write_photo(path, size=(3000, 2000), color=(200, 30, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "Pixel 9"  # Hersteller -- steht fuer alle Metadaten
    img.save(path, "JPEG", exif=exif)


def test_export_keeps_photo_order_downscales_and_strips_metadata(db, tmp_path):
    listing, item = _draft(db, photos=0)
    storage = tmp_path / "inventory_photos"
    # absichtlich in vertauschter Reihenfolge angelegt
    for name, order, color in (("b.jpg", 1, (0, 0, 255)), ("a.jpg", 0, (255, 0, 0))):
        db.add(InventoryPhoto(item_id=item.id, filename=name, sort_order=order))
        _write_photo(storage / str(item.id) / name, color=color)
    db._session.flush()
    db._session.refresh(item)

    paths = ka_posting.export_photos(item, storage, tmp_path / "export" / "job_1")

    assert [p.name for p in paths] == ["01.jpg", "02.jpg"]
    with Image.open(paths[0]) as first:
        assert max(first.size) == ka_posting.PHOTO_MAX_DIMENSION
        assert first.getpixel((10, 10))[0] > 200  # a.jpg (rot) kommt zuerst
        assert len(first.getexif()) == 0


def test_export_stops_at_upload_limit_and_skips_missing_files(db, tmp_path):
    listing, item = _draft(db, photos=0)
    storage = tmp_path / "inventory_photos"
    for index in range(KA_MAX_PHOTOS + 3):
        db.add(InventoryPhoto(item_id=item.id, filename=f"{index}.jpg", sort_order=index))
        if index != 0:  # das erste Foto fehlt auf der Platte
            _write_photo(storage / str(item.id) / f"{index}.jpg", size=(400, 300))
    db._session.flush()
    db._session.refresh(item)

    paths = ka_posting.export_photos(item, storage, tmp_path / "export" / "job_2")
    assert len(paths) == KA_MAX_PHOTOS


# ── CLI ──────────────────────────────────────────────────────────────


async def test_cli_next_exports_photos_and_returns_form_payload(db, tmp_path):
    listing, item = _draft(db)
    storage = tmp_path / "inventory_photos"
    _write_photo(storage / str(item.id) / "p0.jpg")
    await cli.cmd_request(db, NOW, listing.id, dry_run=True)

    output = await cli.cmd_next(db, NOW, storage, tmp_path / "export")
    payload = output["job"]
    assert payload["dry_run"] is True
    assert payload["title"] == "LEGO 75192 Millennium Falcon NEU OVP"
    assert payload["price"] == 650
    assert payload["price_type"] == "VB"
    assert payload["condition"] == "Neu"
    assert payload["category"] == ka_posting.KA_LEGO_CATEGORY
    assert len(payload["photos"]) == 1

    done = await cli.cmd_draft_saved(db, NOW, payload["job_id"], "Testlauf", tmp_path / "export")
    assert done["status"] == PostingJobStatus.DRAFT_SAVED.value
    assert not cli.export_dir(payload["job_id"], tmp_path / "export").exists()


async def test_cli_next_reports_why_nothing_runs(db, tmp_path):
    output = await cli.cmd_next(db, NOW, tmp_path, tmp_path / "export")
    assert output["job"] is None
    assert output["reason"] == "Kein freigegebener Auftrag"


async def test_cli_done_with_wrong_url_keeps_job_running(db, tmp_path):
    listing, item = _draft(db)
    storage = tmp_path / "inventory_photos"
    _write_photo(storage / str(item.id) / "p0.jpg")
    job = await ka_posting.request_posting(db, listing, item, dry_run=False, now=NOW)
    ka_posting.approve(job, NOW)
    await db.commit()
    output = await cli.cmd_next(db, NOW, storage, tmp_path / "export")

    with pytest.raises(PostingError, match="gehört nicht zu"):
        await cli.cmd_done(db, NOW, output["job"]["job_id"], "https://www.ebay.de/itm/1", tmp_path / "export")
    assert job.status == PostingJobStatus.IN_PROGRESS.value

    result = await cli.cmd_done(db, NOW, job.id, KA_URL, tmp_path / "export")
    assert result["status"] == PostingJobStatus.POSTED.value
    rows = (await db.execute(select(Listing).where(Listing.id == listing.id))).scalar_one()
    assert rows.status == ListingStatus.ACTIVE.value


def test_cli_has_no_approve_or_delete_command():
    parser = cli.build_parser()
    commands = parser._subparsers._group_actions[0].choices
    assert set(commands) == {"status", "request", "next", "done", "draft-saved", "fail"}


async def test_draft_cannot_be_deleted_while_agent_posts_it(db):
    from fastapi import HTTPException

    from app.api.routes.listings import delete_listing

    listing, item = _draft(db)
    await ka_posting.request_posting(db, listing, item, dry_run=True, now=NOW)
    await ka_posting.claim_next(db, NOW)
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await delete_listing(item.id, listing.id, session=db)
    assert exc.value.status_code == 409


async def test_routes_request_approve_and_overview(db):
    from app.api.routes import posting_jobs

    listing, item = _draft(db)
    job = await posting_jobs.request_posting(item.id, listing.id, posting_jobs.PostingJobCreate(), session=db)
    assert job.status == PostingJobStatus.REQUESTED.value

    overview = await posting_jobs.posting_overview(session=db)
    assert overview.approval_required is True
    assert overview.max_per_day == 3
    assert [j.id for j in overview.jobs] == [job.id]

    approved = await posting_jobs.approve_posting(job.id, session=db)
    assert approved.status == PostingJobStatus.APPROVED.value
    assert approved.approved_by == "SEBASTIAN"

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await posting_jobs.approve_posting(job.id, session=db)
    assert exc.value.status_code == 400

    cancelled = await posting_jobs.cancel_posting(job.id, session=db)
    assert cancelled.status == PostingJobStatus.CANCELLED.value


async def test_route_rejects_unknown_job_with_404(db):
    from fastapi import HTTPException

    from app.api.routes import posting_jobs

    with pytest.raises(HTTPException) as exc:
        await posting_jobs.approve_posting(999, session=db)
    assert exc.value.status_code == 404


async def test_autopost_setting_accepts_only_true_or_false(db):
    from fastapi import HTTPException

    from app.api.routes.settings import SettingUpdate, update_settings

    with pytest.raises(HTTPException) as exc:
        await update_settings([SettingUpdate(key="ka_autopost_without_approval", value="ja")], session=db)
    assert exc.value.status_code == 400
