"""Scout API for on-demand and cached deal discovery."""

import asyncio
from collections.abc import Collection, Sequence
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.condition import condition_value_factor
from app.domain.identity import is_set_offer
from app.domain.offer_url import canonical_offer_url, offer_identity
from app.engine.decision_engine import analyze_deal
from app.models import DismissedOffer, LegoSet, Offer, OfferPlatform, get_session
from app.scrapers import OFFER_SCRAPERS, PRICE_SCRAPERS
from app.scrapers.base import ScrapedOffer, ScrapedPrice
from app.services.heartbeat import latest_scan_success, load_scan_heartbeats

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
    # Wann zuletzt ein Angebot dieses Feeds als vorhanden bestaetigt wurde.
    # Aus den Angeboten selbst gerechnet, nicht aus einem Heartbeat — deshalb
    # die belastbarere der beiden Zahlen. Siehe last_scan_at.
    last_offer_seen_at: datetime | None = None
    # Wann der letzte Scrape-Lauf Vollzug gemeldet hat. Sagt NICHT, dass er
    # Angebote geholt hat: beide Scrape-Tasks fangen einen 403 ab, setzen
    # `aborted` und kehren normal zurueck, worauf Celery `task_success` feuert.
    # Faellt last_offer_seen_at dahinter zurueck, laeuft die Pipeline zwar,
    # bringt aber nichts mehr mit.
    last_scan_at: datetime | None = None


class DismissRequest(BaseModel):
    """Take one listing out of the feed."""

    platform: str
    offer_url: str
    offer_title: str | None = None
    set_number: str | None = None
    price_eur: float | None = None

    @field_validator("platform")
    @classmethod
    def _known_platform(cls, value: str) -> str:
        """Reject a platform the feed will never match against.

        Without this a typo stores a dismissal that can never take effect: it
        shows up in the dismissed list AND the card stays in the feed, with
        nothing to explain the contradiction.
        """
        candidate = (value or "").strip().upper()
        if candidate not in {platform.value for platform in OfferPlatform}:
            raise ValueError(f"Unbekannte Plattform: {value!r}")
        return candidate


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


# Spaltenbreiten aus dem Modell, nicht getippt: eine Abweichung waere sonst
# erst als StringDataRightTruncation auf Prod aufgefallen.
_IDENTITY_MAX = DismissedOffer.__table__.c.offer_identity.type.length
_TITLE_MAX = DismissedOffer.__table__.c.offer_title.type.length
_SET_NUMBER_MAX = DismissedOffer.__table__.c.set_number.type.length


def _clip(value: str | None, limit: int) -> str | None:
    """Cut a value to what its column holds, deterministically."""
    if value is None:
        return None
    return value[:limit]


def dismissal_key(platform: str | None, offer_url: str | None) -> str:
    """The identity a dismissal is stored under and matched by.

    `offer_identity` is unbounded: `canonical_offer_url` returns unparseable
    input verbatim and keeps the full path for hosts it has no rule for, so a
    725-character identity against a 600-character column is reachable. That
    would make the ✕ answer 500.

    The clipping lives here, in one function both sides call, precisely because
    clipping only on write would be worse than the crash: the feed would go on
    computing the full identity, the stored key would never match, and the
    listing would sit in the dismissed list AND in the feed with nothing to
    explain it.
    """
    return offer_identity(platform, offer_url)[:_IDENTITY_MAX]


def dismissal_values(payload: DismissRequest, now: datetime) -> dict:
    """The row a dismissal writes.

    Dismissing happens from a card whose URL carries the tracking token of the
    run that found it. What gets stored is the canonical identity — otherwise
    the dismissal would already miss on the next run.
    """
    return {
        "offer_identity": dismissal_key(payload.platform, payload.offer_url),
        "platform": payload.platform,
        # Nicht gekuerzt: die Spalte ist Text, und ein abgeschnittener Link
        # taugt nicht mehr zum Wiederfinden im Browser.
        "offer_url": canonical_offer_url(payload.offer_url),
        "offer_title": _clip(payload.offer_title, _TITLE_MAX),
        "set_number": _clip(payload.set_number, _SET_NUMBER_MAX),
        "price_eur": payload.price_eur,
        "dismissed_at": now,
    }


def dismissal_statement(payload: DismissRequest, now: datetime):
    """The upsert a dismissal runs.

    Extracted so a test covers the statement the route actually issues. Asserting
    on a copy built in the test would keep passing after the route switched to a
    plain INSERT — and the second click would start answering 500.
    """
    return (
        pg_insert(DismissedOffer)
        .values(**dismissal_values(payload, now))
        .on_conflict_do_nothing(index_elements=["offer_identity"])
    )


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
    """Build the feed from stored offers — no I/O.

    The database work stays with the caller: the dismissal set and the
    heartbeats come from their own queries, and a function issuing three of
    them could only be tested through a fake session.
    """
    seen_urls: set[str] = set()
    deals: list[DealResult] = []
    total_scanned = 0
    # Aus den Zeilen gerechnet, die ohnehin da sind — keine weitere Abfrage.
    # Abgewaehlte zaehlen mit: gescannt wurden sie, und sonst spraenge die
    # Frischeanzeige zurueck, nur weil man eine Karte weggeklickt hat.
    last_offer_seen_at: datetime | None = None

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
            seller_location=offer.seller_location,
        ):
            continue

        total_scanned += 1
        if offer.last_seen_at and (last_offer_seen_at is None or offer.last_seen_at > last_offer_seen_at):
            last_offer_seen_at = offer.last_seen_at
        # Rows written before URL canonicalisation still hold their tracking
        # tokens, so the raw URL would show one listing several times — each
        # copy with the ROI and score of the run that stored it. Ordered by
        # last_seen_at, the first hit is the freshest analysis.
        dedupe_key = dismissal_key(offer.platform, offer.offer_url)
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
        last_offer_seen_at=last_offer_seen_at,
        last_scan_at=last_scan_at,
    )


async def load_dismissed_identities(session: AsyncSession) -> set[str]:
    """Every dismissed listing's identity."""
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
    heartbeats = await load_scan_heartbeats(session)

    return build_feed(rows, request, dismissed=dismissed, last_scan_at=latest_scan_success(heartbeats))


@router.post("/scan", response_model=ScoutResponse)
async def scout_deals(request: ScoutRequest, session: AsyncSession = Depends(get_session)):
    """Scout multiple sets for profitable deals."""
    if request.cached_only:
        return await _cached_scout_deals(request, session)

    all_deals: list[DealResult] = []
    seen_identities: set[str] = set()
    total_offers = 0
    # Auch hier: abgewaehlt heisst weg. Ohne das galte die Zusage nur fuer
    # einen der drei Endpunkte, und die erste Seite, die scoutScan oder
    # scoutQuick verdrahtet, bekaeme die weggeklickten Inserate zurueck.
    dismissed = await load_dismissed_identities(session)

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
                seller_location=offer.seller_location,
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
                identity = dismissal_key(offer.platform, offer.offer_url)
                if identity in dismissed:
                    continue
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
    # Nicht datetime.now(): scrape_price und scrape_offers fangen jede Ausnahme
    # ab und liefern None bzw. []. Ein Lauf, in dem jeder Scraper geblockt
    # wurde, haette hier "gerade eben gescannt" gemeldet. Was die gespeicherte
    # Pipeline zuletzt geschafft hat, weiss der Heartbeat.
    heartbeats = await load_scan_heartbeats(session)
    return ScoutResponse(
        deals=all_deals[:FEED_LIMIT],
        total_scanned=total_offers,
        sets_analyzed=len(request.set_numbers),
        last_scan_at=latest_scan_success(heartbeats),
    )


@router.post("/dismiss", response_model=DismissedOfferResponse)
async def dismiss_offer(payload: DismissRequest, session: AsyncSession = Depends(get_session)):
    """Take a listing out of the feed for good.

    Idempotent: dismissing twice is the same state, not an error. The stored
    row stays untouched — the first dismissal is the interesting timestamp.
    """
    if not payload.offer_url.strip():
        raise HTTPException(status_code=422, detail="Ohne offer_url gibt es nichts abzuwählen")

    now = datetime.now(UTC)
    identity = dismissal_key(payload.platform, payload.offer_url)
    await session.execute(dismissal_statement(payload, now))
    await session.commit()

    result = await session.execute(select(DismissedOffer).where(DismissedOffer.offer_identity == identity))
    stored = result.scalar_one_or_none()
    if stored is None:
        # Der Upsert garantiert die Zeile zum Commit, nicht zum SELECT: ein
        # paralleler Restore aus dem zweiten Tab kann dazwischenfahren. Dann
        # ist der Wunsch des Aufrufers nicht erfuellt — das gehoert gesagt,
        # nicht als NoResultFound-500 serviert.
        raise HTTPException(status_code=409, detail="Abwahl wurde parallel zurückgenommen")
    return DismissedOfferResponse.model_validate(stored)


@router.get("/dismissed", response_model=list[DismissedOfferResponse])
async def list_dismissed(session: AsyncSession = Depends(get_session)):
    """Every dismissed listing, most recently dismissed first."""
    result = await session.execute(select(DismissedOffer).order_by(DismissedOffer.dismissed_at.desc()))
    return [DismissedOfferResponse.model_validate(row) for row in result.scalars().all()]


@router.delete("/dismissed/{dismissal_id}")
async def restore_offer(dismissal_id: int, session: AsyncSession = Depends(get_session)):
    """Undo a dismissal — the listing returns with the next feed poll.

    404s on an unknown id like every other DELETE route in this API. Answering
    "restored" to a click that changed nothing would leave the caller unable to
    tell the two apart, and the other tab already having restored it is exactly
    when that matters.
    """
    result = await session.execute(select(DismissedOffer).where(DismissedOffer.id == dismissal_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Abwahl nicht gefunden")

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
    # model_copy statt Feld-fuer-Feld: der Nachbau musste bei jedem neuen
    # Antwortfeld von Hand nachgezogen werden — diese Aenderung hat das
    # einmal bezahlt, die naechste haette es wieder getan.
    return response.model_copy(update={"deals": response.deals[:max_results], "sets_analyzed": 1})
