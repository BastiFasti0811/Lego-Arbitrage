"""Kaufentscheidungen zu gemeldeten Angeboten festhalten.

"Gekauft" und "Abgelehnt" kommen aus dem Telegram-Alert. Abgelehnt nimmt das
Inserat zusätzlich aus dem Live Feed (dieselbe Abwahl wie das ✕ in der App),
"Rückgängig" holt es zurück. Die Entscheidungen zählt der Wochen-Digest
(goal_progress), damit sichtbar wird, was aus den gemeldeten Deals wurde.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.offer_url import offer_identity
from app.models import DismissedOffer, LegoSet, Offer
from app.models.dismissal import dismissal_key
from app.models.offer_decision import DecisionKind, DecisionSource, OfferDecision

_IDENTITY_MAX = OfferDecision.__table__.c.offer_identity.type.length


def decision_key(platform: str | None, offer_url: str | None) -> str:
    return offer_identity(platform, offer_url)[:_IDENTITY_MAX]


@dataclass(frozen=True)
class DecisionOutcome:
    """Was ein Knopfdruck bewirkt hat; `decision` ist None nach "Rückgängig"."""

    found: bool
    decision: str | None = None
    previous: str | None = None
    set_number: str | None = None


async def load_offer(session: AsyncSession, offer_id: int) -> tuple[Offer, LegoSet] | None:
    query = select(Offer, LegoSet).join(LegoSet, Offer.set_id == LegoSet.id).where(Offer.id == offer_id)
    row = (await session.execute(query)).first()
    return (row[0], row[1]) if row else None


async def _stored(session: AsyncSession, offer: Offer) -> OfferDecision | None:
    key = decision_key(offer.platform, offer.offer_url)
    query = select(OfferDecision).where(OfferDecision.offer_identity == key)
    return (await session.execute(query)).scalar_one_or_none()


async def _dismiss(session: AsyncSession, offer: Offer, set_number: str, now: datetime) -> None:
    key = dismissal_key(offer.platform, offer.offer_url)
    exists = (await session.execute(select(DismissedOffer.id).where(DismissedOffer.offer_identity == key))).first()
    if exists:
        return
    session.add(
        DismissedOffer(
            offer_identity=key,
            platform=offer.platform,
            offer_url=offer.offer_url,
            offer_title=(offer.offer_title or "")[:500] or None,
            set_number=set_number,
            price_eur=offer.price_eur,
            dismissed_at=now,
        )
    )


async def _restore(session: AsyncSession, offer: Offer) -> None:
    key = dismissal_key(offer.platform, offer.offer_url)
    await session.execute(delete(DismissedOffer).where(DismissedOffer.offer_identity == key))


async def record_decision(
    session: AsyncSession,
    offer_id: int,
    decision: DecisionKind,
    now: datetime,
    source: DecisionSource = DecisionSource.TELEGRAM,
) -> DecisionOutcome:
    """Entscheidung setzen oder ändern. Idempotent: derselbe Knopf zweimal ist
    derselbe Zustand. Committet selbst."""
    loaded = await load_offer(session, offer_id)
    if loaded is None:
        return DecisionOutcome(found=False)
    offer, lego_set = loaded

    stored = await _stored(session, offer)
    previous = stored.decision if stored else None

    if stored is None:
        stored = OfferDecision(
            offer_identity=decision_key(offer.platform, offer.offer_url),
            platform=offer.platform,
            offer_url=offer.offer_url,
            source=source.value,
            decision=decision.value,
            decided_at=now,
        )
        session.add(stored)
    elif stored.decision != decision.value:
        stored.decision = decision.value
        stored.source = source.value
        stored.decided_at = now
    stored.offer_id = offer.id
    stored.set_number = lego_set.set_number
    stored.price_eur = offer.price_eur
    stored.recommendation = offer.recommendation

    if decision == DecisionKind.REJECTED:
        await _dismiss(session, offer, lego_set.set_number, now)
    elif previous == DecisionKind.REJECTED.value:
        # Erst abgelehnt, dann doch gekauft: die Abwahl stammte von hier.
        await _restore(session, offer)

    await session.commit()
    return DecisionOutcome(found=True, decision=decision.value, previous=previous, set_number=lego_set.set_number)


async def undo_decision(session: AsyncSession, offer_id: int) -> DecisionOutcome:
    """Entscheidung zurücknehmen; eine Ablehnung gibt das Inserat im Feed wieder frei."""
    loaded = await load_offer(session, offer_id)
    if loaded is None:
        return DecisionOutcome(found=False)
    offer, lego_set = loaded

    stored = await _stored(session, offer)
    previous = stored.decision if stored else None
    if stored is not None:
        await session.delete(stored)
        if previous == DecisionKind.REJECTED.value:
            await _restore(session, offer)
        await session.commit()
    return DecisionOutcome(found=True, decision=None, previous=previous, set_number=lego_set.set_number)


async def current_decision(session: AsyncSession, offer: Offer) -> str | None:
    stored = await _stored(session, offer)
    return stored.decision if stored else None
