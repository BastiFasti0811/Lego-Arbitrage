"""Inventory API - portfolio tracking and sell-signal management."""

from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import date, datetime
from pathlib import Path
from shutil import rmtree
from urllib.parse import urlencode
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AnalysisHistoryEntry, DealFeedback, LegoSet, get_session
from app.models.inventory import InventoryItem, InventoryStatus
from app.models.inventory_photo import InventoryPhoto

logger = structlog.get_logger()
router = APIRouter()

PHOTO_STORAGE_ROOT = Path(settings.media_root) / "inventory_photos"
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_PHOTOS_PER_ITEM = settings.inventory_photo_max_count
MAX_PHOTO_BYTES = settings.inventory_photo_max_bytes


class InventoryPhotoUpload(BaseModel):
    filename: str
    content_type: str | None = None
    data_url: str


class InventoryPhotoUploadRequest(BaseModel):
    photos: list[InventoryPhotoUpload]


class InventoryAdd(BaseModel):
    set_number: str
    set_name: str
    theme: str | None = None
    image_url: str | None = None
    buy_price: float
    buy_shipping: float = 0.0
    buy_date: date
    buy_platform: str | None = None
    buy_url: str | None = None
    condition: str = "NEW_SEALED"
    quantity: int = 1
    notes: str | None = None


class InventoryUpdate(BaseModel):
    set_name: str | None = None
    theme: str | None = None
    image_url: str | None = None
    buy_price: float | None = None
    buy_shipping: float | None = None
    buy_date: date | None = None
    buy_platform: str | None = None
    buy_url: str | None = None
    condition: str | None = None
    quantity: int | None = None
    notes: str | None = None


class SellRequest(BaseModel):
    sell_price: float
    sell_date: date | None = None
    sell_platform: str | None = None


class InventoryPhotoResponse(BaseModel):
    id: int
    original_filename: str | None
    content_type: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class InventoryResponse(BaseModel):
    id: int
    set_number: str
    set_name: str
    theme: str | None
    image_url: str | None
    buy_price: float
    buy_shipping: float
    total_invested: float
    buy_date: date
    buy_platform: str | None
    buy_url: str | None
    condition: str
    quantity: int = 1
    notes: str | None
    photos: list[InventoryPhotoResponse] = []
    current_market_price: float | None
    market_price_updated_at: datetime | None
    unrealized_profit: float | None
    unrealized_roi_percent: float | None
    sell_signal_active: bool
    sell_signal_reason: str | None
    status: str
    sell_price: float | None
    sell_date: date | None
    sell_platform: str | None
    realized_profit: float | None
    realized_roi_percent: float | None
    holding_days: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PortfolioSummary(BaseModel):
    total_items: int
    holding_items: int
    sold_items: int
    total_invested: float
    current_value: float
    unrealized_profit: float
    unrealized_roi_percent: float
    total_realized_profit: float
    sell_signals_active: int


class SellLinksResponse(BaseModel):
    ebay_url: str
    ebay_title: str
    kleinanzeigen_text: str
    suggested_price: float
    suggested_title: str


@router.get("/platforms")
async def list_platforms(session: AsyncSession = Depends(get_session)):
    """Get all previously used buy/sell platforms for dropdown auto-complete."""
    result = await session.execute(
        select(InventoryItem.buy_platform)
        .where(InventoryItem.buy_platform.is_not(None))
        .distinct()
    )
    buy_platforms = [r[0] for r in result.all() if r[0]]

    result2 = await session.execute(
        select(InventoryItem.sell_platform)
        .where(InventoryItem.sell_platform.is_not(None))
        .distinct()
    )
    sell_platforms = [r[0] for r in result2.all() if r[0]]

    return sorted(set(buy_platforms + sell_platforms))


@router.get("/", response_model=list[InventoryResponse])
async def list_inventory(
    status: str | None = Query(default=None),
    sort_by: str = Query(default="buy_date"),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    query = select(InventoryItem)
    if status:
        query = query.where(InventoryItem.status == status)
    query = query.order_by(InventoryItem.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(query)
    items = result.scalars().all()
    return [_to_response(item) for item in items]


@router.post("/", response_model=InventoryResponse)
async def add_inventory_item(data: InventoryAdd, session: AsyncSession = Depends(get_session)):
    item = InventoryItem(
        set_number=data.set_number,
        set_name=data.set_name,
        theme=data.theme,
        image_url=data.image_url,
        buy_price=data.buy_price,
        buy_shipping=data.buy_shipping,
        buy_date=data.buy_date,
        buy_platform=data.buy_platform,
        buy_url=data.buy_url,
        condition=data.condition,
        quantity=data.quantity,
        notes=data.notes,
        status=InventoryStatus.HOLDING.value,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    logger.info("inventory.added", set_number=data.set_number, buy_price=data.buy_price)
    return _to_response(item)


@router.post("/{item_id}/photos", response_model=list[InventoryPhotoResponse])
async def upload_inventory_photos(
    item_id: int,
    payload: InventoryPhotoUploadRequest,
    session: AsyncSession = Depends(get_session),
):
    item = await _get_item(item_id, session)
    uploads = payload.photos or []
    if not uploads:
        raise HTTPException(status_code=400, detail="Keine Fotos übergeben")
    if len(item.photos) + len(uploads) > MAX_PHOTOS_PER_ITEM:
        raise HTTPException(
            status_code=400,
            detail=f"Maximal {MAX_PHOTOS_PER_ITEM} Fotos pro Inventar-Eintrag erlaubt",
        )

    photo_dir = _photo_dir(item_id)
    photo_dir.mkdir(parents=True, exist_ok=True)
    written_files: list[Path] = []
    created_photos: list[InventoryPhoto] = []

    try:
        next_sort_order = len(item.photos)
        for offset, upload in enumerate(uploads):
            content_type, raw_bytes = _decode_photo_payload(upload)
            suffix = ALLOWED_IMAGE_TYPES.get(content_type, _guess_suffix(upload.filename))
            stored_filename = f"{uuid4().hex}{suffix}"
            file_path = photo_dir / stored_filename
            file_path.write_bytes(raw_bytes)
            written_files.append(file_path)

            photo = InventoryPhoto(
                item_id=item.id,
                filename=stored_filename,
                original_filename=_clean_original_filename(upload.filename),
                content_type=content_type,
                sort_order=next_sort_order + offset,
            )
            session.add(photo)
            created_photos.append(photo)

        await session.commit()
    except Exception:
        await session.rollback()
        for path in written_files:
            path.unlink(missing_ok=True)
        raise

    result = await session.execute(
        select(InventoryPhoto)
        .where(InventoryPhoto.item_id == item_id)
        .order_by(InventoryPhoto.sort_order.asc(), InventoryPhoto.id.asc())
    )
    return [_to_photo_response(photo) for photo in result.scalars().all()]


@router.get("/{item_id}/photos/{photo_id}")
async def get_inventory_photo(
    item_id: int,
    photo_id: int,
    session: AsyncSession = Depends(get_session),
):
    photo = await _get_photo(item_id, photo_id, session)
    file_path = _photo_dir(item_id) / photo.filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Foto-Datei nicht gefunden")

    return FileResponse(
        file_path,
        media_type=photo.content_type or "application/octet-stream",
        filename=photo.original_filename or photo.filename,
    )


@router.delete("/{item_id}/photos/{photo_id}")
async def delete_inventory_photo(
    item_id: int,
    photo_id: int,
    session: AsyncSession = Depends(get_session),
):
    photo = await _get_photo(item_id, photo_id, session)
    file_path = _photo_dir(item_id) / photo.filename
    await session.delete(photo)
    await session.flush()
    await _reindex_photos(item_id, session)
    await session.commit()
    file_path.unlink(missing_ok=True)
    _cleanup_photo_dir(item_id)
    return {"status": "deleted", "id": photo_id}


@router.post("/{item_id}/photos/{photo_id}/make-primary", response_model=list[InventoryPhotoResponse])
async def make_inventory_photo_primary(
    item_id: int,
    photo_id: int,
    session: AsyncSession = Depends(get_session),
):
    await _get_photo(item_id, photo_id, session)
    photos = await _reindex_photos(item_id, session, primary_photo_id=photo_id)
    await session.commit()
    return [_to_photo_response(photo) for photo in photos]


@router.get("/{item_id}/sell-links", response_model=SellLinksResponse)
async def get_sell_links(item_id: int, session: AsyncSession = Depends(get_session)):
    """Generate pre-filled sell links for eBay and Kleinanzeigen."""
    item = await session.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    suggested_price = item.current_market_price or (item.buy_price * 1.5)
    title = f"LEGO {item.set_number} {item.set_name} NEU OVP"
    if len(title) > 80:
        title = title[:77] + "..."

    ebay_params = {
        "keyword": f"LEGO {item.set_number}",
        "LH_BIN": "1",
    }
    ebay_url = f"https://www.ebay.de/sell/create?{urlencode(ebay_params)}"

    kleinanzeigen_text = (
        f"{title}\n\n"
        f"LEGO Set {item.set_number} - {item.set_name}\n"
        f"Zustand: Neu & Originalverpackt (OVP)\n"
        f"Preis: {suggested_price:.0f}\u20ac\n\n"
        f"Versand m\u00f6glich."
    )

    return SellLinksResponse(
        ebay_url=ebay_url,
        ebay_title=title,
        kleinanzeigen_text=kleinanzeigen_text,
        suggested_price=round(suggested_price, 2),
        suggested_title=title,
    )


@router.patch("/{item_id}", response_model=InventoryResponse)
async def update_inventory_item(
    item_id: int,
    data: InventoryUpdate,
    session: AsyncSession = Depends(get_session),
):
    item = await _get_item(item_id, session)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await session.commit()
    await session.refresh(item)
    return _to_response(item)


@router.post("/{item_id}/sell", response_model=InventoryResponse)
async def mark_as_sold(
    item_id: int,
    data: SellRequest,
    session: AsyncSession = Depends(get_session),
):
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Item already sold")

    total_invested = item.buy_price + item.buy_shipping
    selling_costs = data.sell_price * 0.129 + 0.35 + data.sell_price * 0.019 + 0.35
    realized_profit = data.sell_price - total_invested - selling_costs

    item.status = InventoryStatus.SOLD.value
    item.sell_price = data.sell_price
    item.sell_date = data.sell_date or date.today()
    item.sell_platform = data.sell_platform
    item.realized_profit = round(realized_profit, 2)
    item.realized_roi_percent = round((realized_profit / total_invested) * 100, 1) if total_invested > 0 else 0
    item.sell_signal_active = False

    analysis_match = await _find_matching_analysis(item, session)
    feedback_set = await _ensure_feedback_set(item, analysis_match, session)
    if feedback_set:
        feedback = DealFeedback(
            set_id=feedback_set.id,
            purchase_price=item.buy_price,
            purchase_shipping=item.buy_shipping or 0.0,
            purchase_date=item.buy_date,
            purchase_platform=item.buy_platform or "UNKNOWN",
            sale_price=item.sell_price,
            sale_fees=round(selling_costs, 2),
            sale_shipping=0.0,
            sale_packaging=0.0,
            sale_date=item.sell_date,
            sale_platform=item.sell_platform,
            predicted_roi=analysis_match.roi_percent if analysis_match else None,
            predicted_risk_score=analysis_match.risk_score if analysis_match else None,
            notes=_build_feedback_notes(item, analysis_match),
        )
        feedback.calculate_outcomes()
        session.add(feedback)

    await session.commit()
    await session.refresh(item)
    logger.info("inventory.sold", set_number=item.set_number, profit=item.realized_profit)
    return _to_response(item)


@router.delete("/{item_id}")
async def delete_inventory_item(item_id: int, session: AsyncSession = Depends(get_session)):
    item = await _get_item(item_id, session)
    await session.delete(item)
    await session.commit()
    photo_dir = _photo_dir(item_id)
    if photo_dir.exists():
        rmtree(photo_dir, ignore_errors=True)
    return {"status": "deleted", "id": item_id}


@router.get("/summary", response_model=PortfolioSummary)
async def portfolio_summary(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(InventoryItem))
    items = result.scalars().all()

    holding = [i for i in items if i.status == InventoryStatus.HOLDING.value]
    sold = [i for i in items if i.status == InventoryStatus.SOLD.value]

    total_invested = sum(i.buy_price + i.buy_shipping for i in holding)
    current_value = sum(i.current_market_price or (i.buy_price + i.buy_shipping) for i in holding)
    unrealized = current_value - total_invested

    return PortfolioSummary(
        total_items=len(items),
        holding_items=len(holding),
        sold_items=len(sold),
        total_invested=round(total_invested, 2),
        current_value=round(current_value, 2),
        unrealized_profit=round(unrealized, 2),
        unrealized_roi_percent=round((unrealized / total_invested) * 100, 1) if total_invested > 0 else 0,
        total_realized_profit=round(sum(i.realized_profit or 0 for i in sold), 2),
        sell_signals_active=sum(1 for i in holding if i.sell_signal_active),
    )


@router.get("/history", response_model=list[InventoryResponse])
async def inventory_history(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(InventoryItem)
        .where(InventoryItem.status == InventoryStatus.SOLD.value)
        .order_by(InventoryItem.sell_date.desc())
    )
    return [_to_response(item) for item in result.scalars().all()]


async def _find_matching_analysis(
    item: InventoryItem,
    session: AsyncSession,
) -> AnalysisHistoryEntry | None:
    result = await session.execute(
        select(AnalysisHistoryEntry)
        .where(AnalysisHistoryEntry.set_number == item.set_number)
        .order_by(AnalysisHistoryEntry.analyzed_at.desc(), AnalysisHistoryEntry.id.desc())
        .limit(25)
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return None

    def score(entry: AnalysisHistoryEntry) -> float:
        total = 0.0
        if item.buy_url and entry.source_url == item.buy_url:
            total += 100.0

        total += max(0.0, 25.0 - abs((entry.offer_price or 0.0) - item.buy_price))

        if item.buy_date and entry.analyzed_at:
            day_delta = abs((entry.analyzed_at.date() - item.buy_date).days)
            total += max(0.0, 15.0 - min(day_delta, 15))

        return total

    best = max(candidates, key=score)
    return best if score(best) > 0 else None


async def _ensure_feedback_set(
    item: InventoryItem,
    analysis_match: AnalysisHistoryEntry | None,
    session: AsyncSession,
) -> LegoSet | None:
    result = await session.execute(
        select(LegoSet).where(LegoSet.set_number == item.set_number)
    )
    lego_set = result.scalar_one_or_none()
    if lego_set:
        return lego_set

    if not analysis_match:
        return None

    lego_set = LegoSet(
        set_number=item.set_number,
        set_name=analysis_match.set_name or item.set_name,
        theme=analysis_match.theme or item.theme or "Unknown",
        release_year=analysis_match.release_year or date.today().year,
        uvp_eur=analysis_match.uvp,
        category=analysis_match.category,
        current_market_price=analysis_match.market_price,
        eol_status="UNKNOWN",
        image_url=None,
    )
    session.add(lego_set)
    await session.flush()
    return lego_set


def _build_feedback_notes(
    item: InventoryItem,
    analysis_match: AnalysisHistoryEntry | None,
) -> str | None:
    notes: list[str] = []
    if item.notes:
        notes.append(item.notes)
    if analysis_match:
        notes.append(
            f"Auto-Feedback aus Check #{analysis_match.id}: {analysis_match.recommendation}"
        )
        if analysis_match.source_url:
            notes.append(f"Quelle: {analysis_match.source_url}")
    return " | ".join(notes) if notes else None


async def _get_item(item_id: int, session: AsyncSession) -> InventoryItem:
    result = await session.execute(
        select(InventoryItem).where(InventoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"Inventory item {item_id} not found")
    return item


async def _get_photo(item_id: int, photo_id: int, session: AsyncSession) -> InventoryPhoto:
    result = await session.execute(
        select(InventoryPhoto).where(
            InventoryPhoto.id == photo_id,
            InventoryPhoto.item_id == item_id,
        )
    )
    photo = result.scalar_one_or_none()
    if not photo:
        raise HTTPException(status_code=404, detail="Foto nicht gefunden")
    return photo


def _decode_photo_payload(upload: InventoryPhotoUpload) -> tuple[str, bytes]:
    if "," not in upload.data_url:
        raise HTTPException(status_code=400, detail=f"Ungültiges Fotoformat für {upload.filename}")

    header, encoded = upload.data_url.split(",", 1)
    content_type = upload.content_type or header.removeprefix("data:").removesuffix(";base64")
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"Nicht unterstützter Bildtyp: {content_type}")

    try:
        raw_bytes = b64decode(encoded, validate=True)
    except BinasciiError as exc:
        raise HTTPException(status_code=400, detail=f"Foto konnte nicht dekodiert werden: {upload.filename}") from exc

    if len(raw_bytes) > MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Foto {upload.filename} ist zu groß (max. {MAX_PHOTO_BYTES // (1024 * 1024)} MB)",
        )

    return content_type, raw_bytes


def _clean_original_filename(filename: str) -> str:
    name = Path(filename or "foto").name.strip()
    return name[:120] or "foto"


def _guess_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix else ".jpg"


def _photo_dir(item_id: int) -> Path:
    return PHOTO_STORAGE_ROOT / str(item_id)


def _cleanup_photo_dir(item_id: int) -> None:
    photo_dir = _photo_dir(item_id)
    if photo_dir.exists() and not any(photo_dir.iterdir()):
        photo_dir.rmdir()


async def _reindex_photos(
    item_id: int,
    session: AsyncSession,
    primary_photo_id: int | None = None,
) -> list[InventoryPhoto]:
    photos = await _list_photos(item_id, session)
    if primary_photo_id is not None:
        photos.sort(key=lambda photo: (photo.id != primary_photo_id, photo.sort_order, photo.id))
    for index, photo in enumerate(photos):
        photo.sort_order = index
    return photos


async def _list_photos(item_id: int, session: AsyncSession) -> list[InventoryPhoto]:
    result = await session.execute(
        select(InventoryPhoto)
        .where(InventoryPhoto.item_id == item_id)
        .order_by(InventoryPhoto.sort_order.asc(), InventoryPhoto.id.asc())
    )
    return list(result.scalars().all())


def _to_photo_response(photo: InventoryPhoto) -> InventoryPhotoResponse:
    return InventoryPhotoResponse(
        id=photo.id,
        original_filename=photo.original_filename,
        content_type=photo.content_type,
        created_at=photo.created_at,
    )


def _to_response(item: InventoryItem) -> InventoryResponse:
    total_invested = item.buy_price + (item.buy_shipping or 0)
    holding_days = (date.today() - item.buy_date).days if item.buy_date else 0

    return InventoryResponse(
        id=item.id,
        set_number=item.set_number,
        set_name=item.set_name,
        theme=item.theme,
        image_url=item.image_url,
        buy_price=item.buy_price,
        buy_shipping=item.buy_shipping or 0,
        total_invested=round(total_invested, 2),
        buy_date=item.buy_date,
        buy_platform=item.buy_platform,
        buy_url=item.buy_url,
        condition=item.condition,
        quantity=item.quantity or 1,
        notes=item.notes,
        photos=[_to_photo_response(photo) for photo in item.photos],
        current_market_price=item.current_market_price,
        market_price_updated_at=item.market_price_updated_at,
        unrealized_profit=item.unrealized_profit,
        unrealized_roi_percent=item.unrealized_roi_percent,
        sell_signal_active=item.sell_signal_active,
        sell_signal_reason=item.sell_signal_reason,
        status=item.status,
        sell_price=item.sell_price,
        sell_date=item.sell_date,
        sell_platform=item.sell_platform,
        realized_profit=item.realized_profit,
        realized_roi_percent=item.realized_roi_percent,
        holding_days=holding_days,
        created_at=item.created_at,
    )
