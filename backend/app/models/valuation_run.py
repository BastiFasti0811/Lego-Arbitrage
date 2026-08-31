"""Protokoll der Bewertungslaeufe.

Der Lauf meldete bisher `{'updated': 3, 'errors': 0}` bei 41 gehaltenen Sets:
jede uebersprungene Bewertung war ein nacktes `continue`, das nirgends ankam.
Diese Tabellen halten fest, was ein Lauf getan hat — und vor allem, warum er
bei einem Set nichts tun konnte.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ValuationTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class ValuationRunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ValuationOutcome(StrEnum):
    VALUED = "valued"
    SKIPPED = "skipped"
    FAILED = "failed"


class ValuationSkipReason(StrEnum):
    """Warum ein Set keinen Marktwert bekam.

    Jeder Wert entspricht genau einer Aussteige-Stelle im Bewertungslauf.
    """

    NO_PRICES = "no_prices"
    ZERO_CONSENSUS = "zero_consensus"
    SINGLE_SOURCE = "single_source"
    DIVERGENCE = "divergence"
    IMPLAUSIBLE_PRICE = "implausible_price"
    EXCEPTION = "exception"


class ValuationRun(Base):
    __tablename__ = "valuation_runs"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    items_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_valued: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["ValuationRunItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<ValuationRun {self.id} {self.status} {self.items_valued}/{self.items_total}>"


class ValuationRunItem(Base):
    __tablename__ = "valuation_run_items"
    __table_args__ = (Index("ix_valuation_run_items_run_id", "run_id"),)

    run_id: Mapped[int] = mapped_column(
        ForeignKey("valuation_runs.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(Integer)
    set_number: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)
    # Je Quelle: gelieferter Preis oder der Grund, warum keiner kam. Hier
    # steckt die Diagnose — nicht in der Zahl, sondern in der Quellenlage.
    sources: Mapped[list | None] = mapped_column(JSON)
    consensus_price: Mapped[float | None] = mapped_column(Float)

    run: Mapped["ValuationRun"] = relationship(back_populates="items")

    def __repr__(self) -> str:
        return f"<ValuationRunItem {self.set_number} {self.outcome} {self.reason or ''}>"
