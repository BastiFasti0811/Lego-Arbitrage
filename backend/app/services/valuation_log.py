"""Buchfuehrung eines Bewertungslaufs.

Getrennt vom Task, weil die Frage "hinterlaesst jede Aussteige-Stelle eine
Zeile?" ohne Scraper beantwortbar sein muss. Der Recorder sammelt im
Speicher und legt am Ende gebuendelt ab — derselbe Rhythmus wie der Lauf
selbst, der ebenfalls einmal am Schluss committet.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.german import format_eur
from app.models.valuation_run import (
    ValuationOutcome,
    ValuationRun,
    ValuationRunItem,
    ValuationSkipReason,
)


@dataclass
class SourceProbe:
    """Was eine Quelle zu einem Set beigetragen hat — oder warum nichts."""

    source: str
    price_eur: float | None = None
    error: str | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "price_eur": self.price_eur,
            "error": self.error,
            "note": self.note,
        }

    def describe(self) -> str:
        if self.price_eur is None:
            return f"{self.source}: {self.error or 'kein Preis'}"
        text = f"{self.source}: {format_eur(self.price_eur)}"
        return f"{text} ({self.note})" if self.note else text


def describe_sources(probes: list[SourceProbe]) -> str:
    """Die Quellenlage in einer Zeile, wie sie in der Oberflaeche steht."""
    return " · ".join(probe.describe() for probe in probes)


@dataclass
class ValuationRunRecorder:
    """Sammelt die Zeilen eines Laufs, bis sie geschrieben werden."""

    rows: list[dict] = field(default_factory=list)

    def _append(
        self,
        *,
        item_id: int | None,
        set_number: str,
        outcome: ValuationOutcome,
        reason: ValuationSkipReason | None,
        probes: list[SourceProbe],
        detail: str | None = None,
        consensus_price: float | None = None,
    ) -> None:
        self.rows.append({
            "item_id": item_id,
            "set_number": set_number,
            "outcome": outcome.value,
            "reason": reason.value if reason else None,
            "detail": detail if detail is not None else describe_sources(probes),
            "sources": [probe.as_dict() for probe in probes],
            "consensus_price": consensus_price,
        })

    def record_valued(
        self, *, item_id: int | None, set_number: str,
        consensus_price: float, probes: list[SourceProbe],
    ) -> None:
        self._append(
            item_id=item_id, set_number=set_number,
            outcome=ValuationOutcome.VALUED, reason=None,
            probes=probes, consensus_price=consensus_price,
        )

    def record_skipped(
        self, *, item_id: int | None, set_number: str,
        reason: ValuationSkipReason, probes: list[SourceProbe],
        consensus_price: float | None = None,
    ) -> None:
        self._append(
            item_id=item_id, set_number=set_number,
            outcome=ValuationOutcome.SKIPPED, reason=reason,
            probes=probes, consensus_price=consensus_price,
        )

    def record_failed(
        self, *, item_id: int | None, set_number: str,
        detail: str, probes: list[SourceProbe],
    ) -> None:
        self._append(
            item_id=item_id, set_number=set_number,
            outcome=ValuationOutcome.FAILED, reason=ValuationSkipReason.EXCEPTION,
            probes=probes, detail=detail,
        )

    def counts(self) -> dict[str, int]:
        outcomes = [row["outcome"] for row in self.rows]
        return {
            "total": len(outcomes),
            "valued": outcomes.count(ValuationOutcome.VALUED.value),
            "skipped": outcomes.count(ValuationOutcome.SKIPPED.value),
            "failed": outcomes.count(ValuationOutcome.FAILED.value),
        }

    async def flush(self, session: AsyncSession, run_id: int) -> None:
        """Zeilen und Zaehler an den Lauf haengen. Committen tut der Aufrufer."""
        for row in self.rows:
            session.add(ValuationRunItem(run_id=run_id, **row))
        run = await session.get(ValuationRun, run_id)
        if run is None:
            return
        counts = self.counts()
        run.items_total = counts["total"]
        run.items_valued = counts["valued"]
        run.items_skipped = counts["skipped"]
        run.items_failed = counts["failed"]


async def delete_runs_older_than(session: AsyncSession, cutoff: datetime) -> int:
    """Alte Laeufe entfernen. Die Item-Zeilen nimmt die Cascade mit."""
    result = await session.execute(
        select(ValuationRun.id).where(ValuationRun.started_at < cutoff)
    )
    stale = [row[0] for row in result.all()]
    if not stale:
        return 0
    await session.execute(delete(ValuationRun).where(ValuationRun.id.in_(stale)))
    return len(stale)
