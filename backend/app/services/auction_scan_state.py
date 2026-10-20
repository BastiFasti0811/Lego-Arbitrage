"""Store scan output and suppress repeated notifications for the same lot."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.auction_deal import AuctionDeal
from app.models.auction_scan import AuctionScanState
from app.models.base import async_session


async def save_scan(platform: str, results: list[dict], errors: list[str]) -> None:
    values = dict(
        platform=platform, scanned_at=datetime.now(UTC), results=results,
        errors=errors, status="FAILED" if errors else "OK",
    )
    stmt = insert(AuctionScanState).values(**values, notified_urls=[])
    stmt = stmt.on_conflict_do_update(index_elements=["platform"], set_=values)
    async with async_session() as session:
        await session.execute(stmt)
        await session.commit()


async def unnotified_results(platform: str, results: list[dict]) -> list[dict]:
    async with async_session() as session:
        state = (await session.execute(select(AuctionScanState).where(
            AuctionScanState.platform == platform,
        ))).scalar_one_or_none()
        seen = set(state.notified_urls or []) if state else set()
    return [item for item in results if item["can_bid_now"] and item["source_url"] not in seen]


async def mark_notified(platform: str, urls: list[str]) -> None:
    async with async_session() as session:
        state = (await session.execute(select(AuctionScanState).where(
            AuctionScanState.platform == platform,
        ).with_for_update())).scalar_one_or_none()
        if state:
            # Bounded history; canonical lot URLs survive title and tracking changes.
            state.notified_urls = list(dict.fromkeys((state.notified_urls or []) + urls))[-2000:]
            await session.commit()


async def record_bid_ready(platform: str, results: list[dict], now: datetime | None = None) -> int:
    """Lose mit Gebotsfreigabe fuer den Deal-Zaehler festhalten, je Los einmal.
    Rueckgabe: Zahl der neu eingetragenen Lose.

    Unabhaengig von der Telegram-Zustellung: gezaehlt wird, was geprueft und
    kaufbar war. Ein spaeterer Scan desselben Loses aendert die Zeile nicht,
    die Woche des ersten Funds bleibt.
    """
    rows = [
        {
            "platform": platform,
            "source_url": item["source_url"],
            "set_number": item.get("set_number"),
            "lot_title": (item.get("lot_title") or "")[:500] or None,
            "current_bid": item.get("current_bid"),
            "max_bid": item.get("recommended_max_bid"),
            "expected_profit": item.get("expected_profit_current"),
            "found_at": now or datetime.now(UTC),
        }
        for item in results
        if item.get("can_bid_now") and item.get("source_url")
    ]
    if not rows:
        return 0
    stmt = (
        insert(AuctionDeal)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["platform", "source_url"])
        .returning(AuctionDeal.id)
    )
    async with async_session() as session:
        inserted = len((await session.execute(stmt)).all())
        await session.commit()
    return inserted
