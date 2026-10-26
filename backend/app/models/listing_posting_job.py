"""Einstell-Auftrag: ein Listing soll per Browser auf Kleinanzeigen landen.

Nachtrag zu ADR 0002 (2026-10-26): Kleinanzeigen hat keine API, also stellt ein
Agent die Anzeige in Sebastians eingeloggtem Chrome ein. Das Backend kann den
Desktop-Browser nicht steuern. Es haelt nur den Auftrag, die Freigabe und die
Leitplanken (Tageslimit, Abstand, Freigabepflicht); den Browser faehrt der
Skill `kleinanzeigen-einstellen`. Ablauf und Grenzen: `app/services/ka_posting.py`.

Loeschen von Anzeigen gibt es hier bewusst nicht, auch nicht als Aktion.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, false, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PostingAction(StrEnum):
    CREATE = "CREATE"  # neue Anzeige aus dem Entwurf
    # Spaeter: PRICE_CHANGE (Preistreppe), REPOST (Neu-Einstellen). Kein DELETE.


class PostingJobStatus(StrEnum):
    REQUESTED = "REQUESTED"  # wartet auf Sebastians Freigabe
    APPROVED = "APPROVED"  # freigegeben, wartet auf den Agenten
    IN_PROGRESS = "IN_PROGRESS"  # Agent hat ihn geholt und fuellt das Formular
    POSTED = "POSTED"  # Anzeige ist online, Listing ist ACTIVE
    DRAFT_SAVED = "DRAFT_SAVED"  # Formular gefuellt, bewusst nicht veroeffentlicht (Testlauf)
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ApprovalSource(StrEnum):
    SEBASTIAN = "SEBASTIAN"  # Freigabe je Anzeige in der App
    AUTO = "AUTO"  # Sebastian hat die Freigabepflicht abgeschaltet
    TESTLAUF = "TESTLAUF"  # Testlauf: nichts geht online, keine Freigabe noetig


OPEN_POSTING_STATUSES: tuple[str, ...] = (
    PostingJobStatus.REQUESTED.value,
    PostingJobStatus.APPROVED.value,
    PostingJobStatus.IN_PROGRESS.value,
)

_OPEN_JOB_SQL = text("status IN ('REQUESTED','APPROVED','IN_PROGRESS')")
_RUNNING_JOB_SQL = text("status = 'IN_PROGRESS'")


class ListingPostingJob(Base):
    __tablename__ = "listing_posting_jobs"
    __table_args__ = (
        # Hoechstens ein offener Auftrag je Listing: ein Doppelklick darf nicht
        # zwei Anzeigen erzeugen.
        Index(
            "uq_listing_posting_jobs_open",
            "listing_id",
            unique=True,
            sqlite_where=_OPEN_JOB_SQL,
            postgresql_where=_OPEN_JOB_SQL,
        ),
        # Hoechstens ein laufender Auftrag ueberhaupt: zwei `next`-Durchgaenge
        # zur selben Zeit (Autopilot und Hand) duerfen nicht zwei Anzeigen
        # gleichzeitig einstellen. Der zweite Commit scheitert an diesem Index.
        Index(
            "uq_listing_posting_jobs_running",
            "status",
            unique=True,
            sqlite_where=_RUNNING_JOB_SQL,
            postgresql_where=_RUNNING_JOB_SQL,
        ),
        Index("ix_listing_posting_jobs_status", "status"),
    )

    # SET NULL statt CASCADE: Wird ein Listing geloescht, bleibt der Auftrag als
    # Historie stehen. Sonst sinken Tageszaehler und Zahl der eingestellten
    # Inserate, und das Tageslimit liesse sich durch Loeschen umgehen.
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), index=True, nullable=True
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Testlauf: Formular fuellen, nicht veroeffentlichen.
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(20))

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    result_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    listing: Mapped["Listing | None"] = relationship()

    def __repr__(self) -> str:
        return f"<ListingPostingJob listing={self.listing_id} {self.action} {self.status}>"


from app.models.listing import Listing  # noqa: E402
