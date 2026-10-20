"""Zielzaehler: Abstand zu den drei Zielzahlen aus Memory projekt-ziel.

- Verkauf: 30 von 49 Lego-Posten inseriert, 15 verkauft (Stand gesamt).
- Zukauf: 3 gepruefte, kaufbare Deals pro Woche.

Eine Quelle fuer App (Inventar-Kopf), Montags-Digest, Tagesbericht und das
Laufprotokoll der Nachtlaeufe (`python -m app.tools.goal_progress`).

Gepruefter Deal heisst:
- ein Angebot mit GO oder GO_STAR. Die Engine vergibt beides nur bei
  mindestens zwei Preisquellen (sonst CHECK), siehe decision_engine;
- ein Auktionslos mit Gebotsfreigabe (`auction_deals`), also nie
  NEEDS_REVIEW und nie allein auf dem BrickMerge-Preis.
CHECK-Angebote gehen nicht in den Zaehler; der Digest nennt sie in einer
eigenen Zeile, weil sie wegen telegram_alert_on_go_only nie gemeldet werden.

Wochen sind Kalenderwochen Montag 00:00 bis Montag 00:00 in Europe/Berlin,
dieselbe Zeitzone wie Celery Beat.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.decision_engine import Recommendation
from app.models.auction_deal import AuctionDeal
from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus
from app.models.offer import Offer

BERLIN = ZoneInfo("Europe/Berlin")
DEAL_RECOMMENDATIONS = (Recommendation.GO_STAR, Recommendation.GO)
_COUNTED_RECOMMENDATIONS = (*DEAL_RECOMMENDATIONS, Recommendation.CHECK)


# ── Wochen ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Week:
    start: datetime  # Montag 00:00 Berlin, zeitzonenbewusst
    end: datetime  # folgender Montag 00:00 Berlin (exklusiv)

    @property
    def first_day(self) -> date:
        return self.start.date()

    @property
    def last_day(self) -> date:
        return self.end.date() - timedelta(days=1)

    @property
    def label(self) -> str:
        """"KW 43 (19.10.–25.10.)"."""
        return f"KW {self.first_day.isocalendar().week} ({self.first_day:%d.%m.}–{self.last_day:%d.%m.})"

    def contains(self, moment: datetime) -> bool:
        return self.start <= _aware(moment) < self.end

    def contains_day(self, day: date) -> bool:
        return self.first_day <= day <= self.last_day

    def previous(self) -> "Week":
        return _week_starting(self.first_day - timedelta(days=7))


def _week_starting(monday: date) -> Week:
    # Grenzen als Berliner Wanduhrzeit: ueber die Zeitumstellung hat die Woche
    # 167 oder 169 echte Stunden. Vergleiche mit UTC-Zeitpunkten rechnet
    # Python ueber den jeweiligen Offset korrekt um.
    return Week(
        start=datetime.combine(monday, time(), BERLIN),
        end=datetime.combine(monday + timedelta(days=7), time(), BERLIN),
    )


def week_of(moment: datetime) -> Week:
    local = _aware(moment).astimezone(BERLIN)
    return _week_starting(local.date() - timedelta(days=local.weekday()))


def _aware(moment: datetime) -> datetime:
    # SQLite liefert zeitzonenlose Werte; gespeichert wird immer UTC.
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


# ── Zaehlen (rein, ohne DB) ─────────────────────────────────────────


@dataclass
class DealCount:
    go_star: int = 0
    go: int = 0
    auctions: int = 0
    checks: int = 0  # nicht im Ziel, nur Hinweis

    @property
    def total(self) -> int:
        return self.go_star + self.go + self.auctions


@dataclass
class SalesCount:
    listed: int = 0  # Posten, die in dieser Woche zum ersten Mal online gingen
    sold: int = 0


@dataclass
class WeekNumbers:
    week: Week
    deals: DealCount
    sales: SalesCount


def was_ever_listed(item) -> bool:
    """Ein Listing mit Einstell-Datum gilt als inseriert, auch wenn es inzwischen
    beendet oder verkauft ist. Reine Entwuerfe zaehlen nicht."""
    return any(listing.listed_at is not None for listing in item.listings)


def first_listed_on(item) -> date | None:
    days = [listing.listed_at for listing in item.listings if listing.listed_at is not None]
    return min(days) if days else None


def lego_listing_counts(items) -> dict[str, int]:
    """Gesamtstand Verkauf. Posten, nicht Stueck; Entwurfs-Posten zaehlen nicht."""
    lego = [
        i for i in items
        if i.item_type == InventoryItemType.LEGO.value and i.status != InventoryStatus.DRAFT.value
    ]
    return {
        "lego_items": len(lego),
        "lego_listed": sum(1 for i in lego if was_ever_listed(i)),
        "lego_sold": sum(1 for i in lego if i.status == InventoryStatus.SOLD.value),
    }


def count_deals(offers, auction_deals, week: Week) -> DealCount:
    """`offers`: Zeilen mit recommendation und discovered_at; `auction_deals`: mit found_at."""
    count = DealCount()
    for offer in offers:
        if not week.contains(offer.discovered_at):
            continue
        if offer.recommendation == Recommendation.GO_STAR:
            count.go_star += 1
        elif offer.recommendation == Recommendation.GO:
            count.go += 1
        elif offer.recommendation == Recommendation.CHECK:
            count.checks += 1
    count.auctions = sum(1 for deal in auction_deals if week.contains(deal.found_at))
    return count


def count_sales(items, week: Week) -> SalesCount:
    count = SalesCount()
    for item in items:
        if item.item_type != InventoryItemType.LEGO.value or item.status == InventoryStatus.DRAFT.value:
            continue
        listed_on = first_listed_on(item)
        if listed_on is not None and week.contains_day(listed_on):
            count.listed += 1
        if item.status == InventoryStatus.SOLD.value and item.sell_date and week.contains_day(item.sell_date):
            count.sold += 1
    return count


def week_numbers(items, offers, auction_deals, week: Week) -> WeekNumbers:
    return WeekNumbers(week=week, deals=count_deals(offers, auction_deals, week), sales=count_sales(items, week))


@dataclass
class GoalProgress:
    now: datetime
    lego_items: int
    lego_listed: int
    lego_sold: int
    current: WeekNumbers  # laufende Woche bis jetzt
    previous: WeekNumbers  # letzte abgeschlossene Woche
    goal_listed: int = field(default_factory=lambda: settings.goal_listed)
    goal_sold: int = field(default_factory=lambda: settings.goal_sold)
    goal_deals_per_week: int = field(default_factory=lambda: settings.goal_deals_per_week)
    deadline: date = field(default_factory=lambda: settings.goal_deadline)

    @property
    def days_left(self) -> int:
        return max(0, (self.deadline - self.now.astimezone(BERLIN).date()).days)

    @property
    def weeks_left(self) -> int:
        # Angebrochene Woche zaehlt voll: am Montag vor der Frist bleibt eine.
        return -(-self.days_left // 7)

    @property
    def listed_missing(self) -> int:
        return max(0, self.goal_listed - self.lego_listed)

    @property
    def sold_missing(self) -> int:
        return max(0, self.goal_sold - self.lego_sold)

    def per_week_needed(self, missing: int) -> float | None:
        """Noetiges Tempo bis zur Frist; None, wenn die Frist vorbei ist."""
        if missing == 0:
            return 0.0
        return missing / self.weeks_left if self.weeks_left else None


def build_progress(items, offers, auction_deals, now: datetime) -> GoalProgress:
    current = week_of(now)
    totals = lego_listing_counts(items)
    return GoalProgress(
        now=now,
        **totals,
        current=week_numbers(items, offers, auction_deals, current),
        previous=week_numbers(items, offers, auction_deals, current.previous()),
    )


@dataclass
class GoalDigest:
    """Montags-Digest: die abgeschlossene Woche gegen die Woche davor, dazu der Gesamtstand."""

    progress: GoalProgress
    reported: WeekNumbers
    before: WeekNumbers


def build_digest(items, offers, auction_deals, now: datetime) -> GoalDigest:
    progress = build_progress(items, offers, auction_deals, now)
    reported = progress.previous
    return GoalDigest(
        progress=progress,
        reported=reported,
        before=week_numbers(items, offers, auction_deals, reported.week.previous()),
    )


def protocol_segment(progress: GoalProgress) -> str:
    """Das Feld "Zielstand" einer Laufprotokoll-Zeile (Memory laufprotokoll)."""
    return (
        f"Zielstand: {progress.lego_listed}/{progress.goal_listed}, "
        f"{progress.lego_sold}/{progress.goal_sold}, "
        f"Deals diese Woche {progress.current.deals.total}/{progress.goal_deals_per_week}"
    )


# ── Laden ───────────────────────────────────────────────────────────


def deal_offers_query(since: datetime):
    return select(Offer.recommendation, Offer.discovered_at).where(
        Offer.discovered_at >= since,
        Offer.recommendation.in_(_COUNTED_RECOMMENDATIONS),
    )


def auction_deals_query(since: datetime):
    return select(AuctionDeal.found_at).where(AuctionDeal.found_at >= since)


def lego_items_query():
    return select(InventoryItem).where(
        InventoryItem.item_type == InventoryItemType.LEGO.value,
        InventoryItem.status != InventoryStatus.DRAFT.value,
    )


async def load_rows(session: AsyncSession, since: datetime) -> tuple[list, list, list]:
    """Inventar komplett (Gesamtstand), Deals ab `since`. Bei 125 Posten und
    einigen hundert Angeboten je Woche ist Zaehlen in Python billig."""
    items = (await session.execute(lego_items_query())).scalars().all()
    offers = (await session.execute(deal_offers_query(since))).all()
    auction_deals = (await session.execute(auction_deals_query(since))).all()
    return list(items), list(offers), list(auction_deals)


async def load_goal_progress(session: AsyncSession, now: datetime | None = None) -> GoalProgress:
    now = now or datetime.now(UTC)
    since = week_of(now).previous().start
    items, offers, auction_deals = await load_rows(session, since)
    return build_progress(items, offers, auction_deals, now)


async def load_goal_digest(session: AsyncSession, now: datetime | None = None) -> GoalDigest:
    now = now or datetime.now(UTC)
    since = week_of(now).previous().previous().start
    items, offers, auction_deals = await load_rows(session, since)
    return build_digest(items, offers, auction_deals, now)
