"""Scout API for on-demand and cached deal discovery."""

import asyncio
from collections.abc import Collection, Sequence
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.condition import condition_value_factor
from app.domain.identity import is_set_offer
from app.domain.offer_url import canonical_offer_url, offer_identity
from app.engine.decision_engine import analyze_deal
from app.models import DismissedOffer, LegoSet, Offer, get_session
from app.scrapers import OFFER_SCRAPERS, PRICE_SCRAPERS
from app.scrapers.base import ScrapedOffer, ScrapedPrice
from app.services.heartbeat import latest_scan_success, load_heartbeats

logger = structlog.get_logger()
router = APIRouter()

# So viele Karten zeigt der Feed. Der Deckel greift nach dem Abwahl-Filter,
# sonst hielte ein ausgeblendetes Inserat seinen Platz einfach leer.
FEED_LIMIT = 20


class ScoutRequest(BaseModel):
    """Request to scout for deals."""

    set_numbers: list[str]
    max_budget: float | None = None
    min_roi: float = 15.0
    cached_only: bool = False


class DealResult(BaseModel):
    """A discovered deal."""

    set_number: str
    platform: str
    offer_title: str
    offer_url: str
    price: float
    shipping: float | None
    market_price: float
    estimated_roi: float
    risk_score: int
    recommendation: str
    reason: str
    opportunity_score: float
    set_name: str | None = None
    theme: str | None = None
    # Ohne Zustand und Alter ist eine Karte nicht bewertbar: derselbe Preis
    # bedeutet bei "neu versiegelt" etwas anderes als bei "gebraucht", und ein
    # zwei Wochen alter Fund ist meistens längst weg.
    condition: str | None = None
    last_seen_at: datetime | None = None
    # Was das Exemplar in seinem Zustand erloest. Weicht es vom Marktpreis
    # ab, waere der ROI ohne diese Zahl nicht nachvollziehbar.
    expected_sale_price: float | None = None
    # Die Basis, auf die sich expected_sale_price bezieht. market_price ist im
    # Live-Pfad der Konsens, die Erloesrechnung laeuft aber gegen die Referenz
    # (bei Ware im Handel die UVP) — ohne dieses Feld stuenden auf der Karte
    # zwei Zahlen ohne gemeinsamen Nenner.
    reference_price: float | None = None


class ScoutResponse(BaseModel):
    """Scout results."""

    deals: list[DealResult]
    total_scanned: int
    sets_analyzed: int
    # Wann der letzte Angebots-Scan durchlief. Nicht zu verwechseln mit dem
    # Zeitpunkt dieses Requests: der Feed pollt alle 30 s, gescannt wird alle
    # zwei bis sechs Stunden. Steht hier None, hat noch nie ein Lauf gemeldet.
    last_scan_at: datetime | None = None


class DismissRequest(BaseModel):
    """Ein Inserat aus dem Feed nehmen."""

    platform: str
    offer_url: str
    offer_title: str | None = None
    set_number: str | None = None
    price_eur: float | None = None


class DismissedOfferResponse(BaseModel):
    """Ein ausgeblendetes Inserat, wie es beim Abwählen aussah."""

    id: int
    platform: str
    offer_url: str
    offer_title: str | None
    set_number: str | None
    price_eur: float | None
    dismissed_at: datetime

    model_config = {"from_attributes": True}


def dismissal_values(payload: DismissRequest, now: datetime) -> dict:
    """Die Zeilenwerte einer Abwahl.

    Abgewählt wird von einer Karte aus, deren URL den Tracking-Token des Laufs
    trägt, der sie gefunden hat. Gespeichert wird die kanonische Identität —
    sonst griffe die Abwahl schon beim nächsten Lauf nicht mehr.
    """
    return {
        "offer_identity": offer_identity(payload.platform, payload.offer_url),
        "platform": (payload.platform or "").upper(),
        "offer_url": canonical_offer_url(payload.offer_url),
        "offer_title": payload.offer_title,
        "set_number": payload.set_number,
        "price_eur": payload.price_eur,
        "dismissed_at": now,
    }


def _build_deal_result(offer: Offer, lego_set: LegoSet) -> DealResult:
    estimated_roi = offer.estimated_roi or 0.0
    risk_score = offer.risk_score or 10
    opportunity_score = round(max(0.0, estimated_roi) * max(0, 10 - risk_score), 1)

    # Ohne Marktpreis und ohne UVP gibt es keine Basis: auf den Angebotspreis
    # zurueckzufallen erzeugte "Marktpreis 10,00 EUR / Erloes 7,00 EUR" neben
    # einem positiven ROI. Dann lieber keine Erloeszeile als eine erfundene.
    reference_price = lego_set.current_market_price or lego_set.uvp_eur
    market_price = reference_price or offer.price_eur
    expected_sale_price = (
        round(reference_price * condition_value_factor(offer.condition, offer.box_damage), 2)
        if reference_price
        else None
    )

    return DealResult(
        set_number=lego_set.set_number,
        set_name=lego_set.set_name,
        theme=lego_set.theme,
        platform=offer.platform,
        offer_title=offer.offer_title,
        offer_url=offer.offer_url,
        price=offer.price_eur,
        shipping=offer.shipping_eur,
        market_price=market_price,
        estimated_roi=estimated_roi,
        risk_score=risk_score,
        recommendation=offer.recommendation or "CHECK",
        reason=offer.analysis_notes or "Analyse noch ausstehend",
        opportunity_score=opportunity_score,
        condition=offer.condition,
        last_seen_at=offer.last_seen_at,
        expected_sale_price=expected_sale_price,
        reference_price=reference_price,
    )


def build_feed(
    rows: Sequence[tuple[Offer, LegoSet]],
    request: ScoutRequest,
    *,
    dismissed: Collection[str] = frozenset(),
    last_scan_at: datetime | None = None,
) -> ScoutResponse:
    """Baut den Feed aus gespeicherten Angeboten — ohne I/O.

    Die Datenbankzugriffe liegen bewusst beim Aufrufer: Abwahl-Liste und
    Heartbeats kommen aus eigenen Abfragen, und eine Funktion, die drei
    Queries absetzt, ließe sich nur noch über eine Fake-Session prüfen.
    """
    seen_urls: set[str] = set()
    deals: list[DealResult] = []
    total_scanned = 0

    for offer, lego_set in rows:
        if request.set_numbers and lego_set.set_number not in request.set_numbers:
            continue
        if request.max_budget and offer.price_eur > request.max_budget:
            continue
        if (offer.estimated_roi or 0.0) < request.min_roi:
            continue

        # Zeilen aus der Zeit vor dem Zubehoerfilter liegen weiter in der DB.
        # Ungeprueft fuellen sie den Feed mit 9,99-EUR-Wandhalterungen, die
        # gegen den Setpreis bewertet als +2973 % erscheinen.
        if not is_set_offer(
            offer.offer_title,
            lego_set.set_number,
            price_eur=offer.price_eur,
            reference_price=lego_set.current_market_price or lego_set.uvp_eur,
            set_name=lego_set.set_name,
        ):
            continue

        total_scanned += 1
        # Rows written before URL canonicalisation still hold their tracking
        # tokens, so the raw URL would show one listing several times — each
        # copy with the ROI and score of the run that stored it. Ordered by
        # last_seen_at, the first hit is the freshest analysis.
        dedupe_key = offer_identity(offer.platform, offer.offer_url)
        # Abgewählt heißt weg — auch wenn der nächste Lauf dieselbe Anzeige
        # unter einer frischen Tracking-URL wiederfindet. Gezählt wurde sie
        # oben trotzdem: geprüft hat der Scan sie ja.
        if dedupe_key in dismissed:
            continue
        if dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        deals.append(_build_deal_result(offer, lego_set))

    deals.sort(key=lambda deal: deal.opportunity_score, reverse=True)
    return ScoutResponse(
        deals=deals[:FEED_LIMIT],
        total_scanned=total_scanned,
        sets_analyzed=len(request.set_numbers),
        last_scan_at=last_scan_at,
    )


async def load_dismissed_identities(session: AsyncSession) -> set[str]:
    """Die Identitäten aller abgewählten Inserate."""
    result = await session.execute(select(DismissedOffer.offer_identity))
    return set(result.scalars().all())


async def _cached_scout_deals(request: ScoutRequest, session: AsyncSession) -> ScoutResponse:
    result = await session.execute(
        select(Offer, LegoSet)
        .join(LegoSet, Offer.set_id == LegoSet.id)
        .where(Offer.status == "ACTIVE")
        .where(Offer.recommendation.is_not(None))
        .order_by(Offer.last_seen_at.desc())
    )
    rows = result.all()
    dismissed = await load_dismissed_identities(session)
    heartbeats = await load_heartbeats(session)

    return build_feed(rows, request, dismissed=dismissed, last_scan_at=latest_scan_success(heartbeats))


@router.post("/scan", response_model=ScoutResponse)
async def scout_deals(request: ScoutRequest, session: AsyncSession = Depends(get_session)):
    """Scout multiple sets for profitable deals."""
    if request.cached_only:
        return await _cached_scout_deals(request, session)

    all_deals: list[DealResult] = []
    seen_identities: set[str] = set()
    total_offers = 0

    for set_number in request.set_numbers:
        logger.info("scout.scanning", set_number=set_number)
        set_result = await session.execute(select(LegoSet).where(LegoSet.set_number == set_number))
        lego_set = set_result.scalar_one_or_none()

        async def scrape_price(scraper_cls) -> ScrapedPrice | None:
            try:
                async with scraper_cls() as scraper:
                    return await scraper.get_price(set_number)
            except Exception as exc:
                logger.warning("scout.price_failed", scraper=scraper_cls.__name__, error=str(exc))
                return None

        price_results = await asyncio.gather(*(scrape_price(scraper_cls) for scraper_cls in PRICE_SCRAPERS))
        prices = [price for price in price_results if price]

        async def scrape_offers(scraper_cls) -> list[ScrapedOffer]:
            try:
                async with scraper_cls() as scraper:
                    return await scraper.get_offers(set_number)
            except Exception as exc:
                logger.warning("scout.offers_failed", scraper=scraper_cls.__name__, error=str(exc))
                return []

        offer_results = await asyncio.gather(*(scrape_offers(scraper_cls) for scraper_cls in OFFER_SCRAPERS))
        offers = [offer for scraper_offers in offer_results for offer in scraper_offers]

        total_offers += len(offers)

        reference_price = (lego_set.current_market_price or lego_set.uvp_eur) if lego_set else None

        for offer in offers:
            if request.max_budget and offer.price_eur > request.max_budget:
                continue

            # Gleicher Zubehoerschutz wie im Scrape-Pfad — sonst meldet der
            # Live-Scout weiterhin Wandhalterungen als Deals.
            if not is_set_offer(
                offer.offer_title,
                set_number,
                price_eur=offer.price_eur,
                reference_price=reference_price,
                set_name=lego_set.set_name if lego_set else None,
            ):
                continue

            analysis = analyze_deal(
                set_number=set_number,
                set_name=lego_set.set_name if lego_set else offer.offer_title,
                release_year=lego_set.release_year if lego_set else 2022,
                theme=lego_set.theme if lego_set else "Unknown",
                offer_price=offer.price_eur,
                prices=prices,
                uvp=lego_set.uvp_eur if lego_set else None,
                eol_status=lego_set.eol_status if lego_set else "UNKNOWN",
                condition=offer.condition,
                box_damage=offer.box_damage,
                purchase_shipping=offer.shipping_eur,
            )

            if analysis.roi.roi_percent >= request.min_roi:
                # Zwei Scraper (und ein doppelter Watchlist-Eintrag) liefern
                # dieselbe Anzeige — im Feed ist sie trotzdem ein Deal.
                identity = offer_identity(offer.platform, offer.offer_url)
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)

                all_deals.append(
                    DealResult(
                        set_number=set_number,
                        set_name=lego_set.set_name if lego_set else None,
                        theme=lego_set.theme if lego_set else None,
                        platform=offer.platform,
                        offer_title=offer.offer_title,
                        offer_url=offer.offer_url,
                        price=offer.price_eur,
                        shipping=offer.shipping_eur,
                        # Dieselbe Basis wie expected_sale_price, sonst stehen
                        # auf der Karte zwei Zahlen ohne gemeinsamen Nenner: bei
                        # Ware im Handel ist die Referenz die UVP, nicht der
                        # Konsens ("Markt 60,00 / Erloes 89,99").
                        market_price=analysis.reference_price,
                        estimated_roi=analysis.roi.roi_percent,
                        risk_score=analysis.risk.total,
                        recommendation=analysis.recommendation,
                        reason=analysis.reason,
                        opportunity_score=analysis.opportunity_score,
                        condition=offer.condition,
                        expected_sale_price=analysis.expected_sale_price,
                        reference_price=analysis.reference_price,
                    )
                )

    all_deals.sort(key=lambda deal: deal.opportunity_score, reverse=True)
    return ScoutResponse(
        deals=all_deals[:FEED_LIMIT],
        total_scanned=total_offers,
        sets_analyzed=len(request.set_numbers),
        # Dieser Pfad hat gerade selbst gescrapt — der Scan ist genau jetzt.
        last_scan_at=datetime.now(UTC),
    )


@router.post("/dismiss", response_model=DismissedOfferResponse)
async def dismiss_offer(payload: DismissRequest, session: AsyncSession = Depends(get_session)):
    """Ein Inserat dauerhaft aus dem Feed nehmen.

    Idempotent: zweimal abwählen ist kein Fehler, sondern derselbe Zustand.
    Die bereits gespeicherte Zeile bleibt dabei unangetastet — der Zeitpunkt
    der ersten Abwahl ist der interessante.
    """
    if not payload.offer_url.strip():
        raise HTTPException(status_code=422, detail="Ohne offer_url gibt es nichts abzuwählen")

    values = dismissal_values(payload, datetime.now(UTC))
    stmt = (
        pg_insert(DismissedOffer)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["offer_identity"])
    )
    await session.execute(stmt)
    await session.commit()

    result = await session.execute(
        select(DismissedOffer).where(DismissedOffer.offer_identity == values["offer_identity"])
    )
    return DismissedOfferResponse.model_validate(result.scalar_one())


@router.get("/dismissed", response_model=list[DismissedOfferResponse])
async def list_dismissed(session: AsyncSession = Depends(get_session)):
    """Alle ausgeblendeten Inserate, zuletzt abgewähltes zuerst."""
    result = await session.execute(select(DismissedOffer).order_by(DismissedOffer.dismissed_at.desc()))
    return [DismissedOfferResponse.model_validate(row) for row in result.scalars().all()]


@router.delete("/dismissed/{dismissal_id}")
async def restore_offer(dismissal_id: int, session: AsyncSession = Depends(get_session)):
    """Eine Abwahl zurücknehmen — das Inserat taucht im nächsten Feed wieder auf.

    Eine unbekannte Id ist kein Fehler: das Ziel ist "nicht mehr abgewählt",
    und das gilt dann bereits. Antwortet wie die übrigen DELETE-Routen mit
    einem kleinen Body — der Frontend-Client liest jede Antwort als JSON.
    """
    await session.execute(delete(DismissedOffer).where(DismissedOffer.id == dismissal_id))
    await session.commit()
    return {"status": "restored", "id": dismissal_id}


@router.get("/quick/{set_number}")
async def quick_scout(
    set_number: str,
    max_results: int = Query(default=10, le=50),
    session: AsyncSession = Depends(get_session),
):
    """Quick scout for a single set."""
    response = await scout_deals(ScoutRequest(set_numbers=[set_number]), session=session)
    return ScoutResponse(
        deals=response.deals[:max_results],
        total_scanned=response.total_scanned,
        sets_analyzed=1,
        last_scan_at=response.last_scan_at,
    )
