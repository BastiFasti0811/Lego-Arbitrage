"""Scheduled scraping tasks that refresh prices and active offers."""

from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy import select

from app.config import settings
from app.domain.identity import is_plausible_price as _is_plausible_price
from app.domain.identity import is_set_offer
from app.domain.offer_url import canonical_offer_url
from app.engine.market_consensus import calculate_consensus
from app.models import LegoSet, Offer, PriceRecord, WatchlistItem
from app.models.base import async_session
from app.scrapers import METADATA_SCRAPERS, OFFER_SCRAPERS, PRICE_SCRAPERS
from app.scrapers.kleinanzeigen import KleinanzeigenScraper
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


def _is_persistable(consensus) -> bool:
    """Whether a consensus is solid enough to store as the set's market price.

    Two independent sources that broadly agree. A single source is a guess,
    and extreme divergence means we do not know which source to believe.
    """
    return consensus.num_sources >= 2 and consensus.divergence_percent <= 0.30


def _apply_set_info(lego_set: LegoSet, info, *, overwrite_uvp: bool = False) -> bool:
    """Merge scraped metadata into a set record."""
    changed = False

    if info.set_name and (
        not lego_set.set_name
        or lego_set.set_name == lego_set.set_number
        or lego_set.set_name == f"LEGO {lego_set.set_number}"
    ):
        lego_set.set_name = info.set_name
        changed = True

    if info.theme and (not lego_set.theme or lego_set.theme == "Unknown"):
        lego_set.theme = info.theme
        changed = True

    if info.release_year and (not lego_set.release_year or lego_set.release_year == 2020):
        lego_set.release_year = info.release_year
        changed = True

    if info.uvp_eur and (overwrite_uvp or not lego_set.uvp_eur):
        lego_set.uvp_eur = info.uvp_eur
        changed = True

    if info.eol_status and info.eol_status != "UNKNOWN" and lego_set.eol_status != info.eol_status:
        lego_set.eol_status = info.eol_status
        changed = True

    if info.growth_percent is not None and lego_set.growth_percent != info.growth_percent:
        lego_set.growth_percent = info.growth_percent
        changed = True

    if info.image_url and not lego_set.image_url:
        lego_set.image_url = info.image_url
        changed = True

    if lego_set.release_year:
        category = lego_set.compute_category().value
        if lego_set.category != category:
            lego_set.category = category
            changed = True

    return changed


@celery_app.task(name="app.tasks.scrape_daily.scrape_set_prices")
def scrape_set_prices(set_number: str) -> dict:
    """Scrape all price sources for a single set."""
    return _run_async(_scrape_set_prices_async(set_number))


async def _scrape_set_prices_async(set_number: str) -> dict:
    """Async implementation of price scraping."""
    results = {"set_number": set_number, "prices": 0, "offers": 0, "errors": [], "blocked": False}

    async with async_session() as session:
        result = await session.execute(select(LegoSet).where(LegoSet.set_number == set_number))
        lego_set = result.scalar_one_or_none()
        if not lego_set:
            results["errors"].append("Set not found in database")
            return results

        now = datetime.now(UTC)
        scraped_prices = []

        for scraper_cls in PRICE_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    price = await scraper.get_price(set_number)
                    if price and not _is_plausible_price(price.price_eur, lego_set.uvp_eur):
                        logger.warning(
                            "scrape.implausible_price",
                            set_number=set_number,
                            source=price.source,
                            price=price.price_eur,
                            uvp=lego_set.uvp_eur,
                        )
                        price = None
                    if price:
                        scraped_prices.append(price)
                        session.add(
                            PriceRecord(
                                set_id=lego_set.id,
                                source=price.source,
                                price_eur=price.price_eur,
                                price_original=getattr(price, "price_original", None),
                                currency=price.currency,
                                condition=price.condition,
                                sold_count=price.sold_count,
                                median_price=price.median_price,
                                min_price=price.min_price,
                                max_price=price.max_price,
                                scraped_at=now,
                                source_url=price.source_url,
                                is_reliable=price.is_reliable,
                                notes=price.notes,
                            )
                        )
                        results["prices"] += 1
            except httpx.HTTPStatusError as exc:
                results["errors"].append(f"{scraper_cls.__name__}: HTTP {exc.response.status_code}")
                if exc.response.status_code in (403, 429):
                    results["blocked"] = True
                    logger.warning(
                        "scrape.price_blocked", scraper=scraper_cls.__name__, status=exc.response.status_code
                    )
                else:
                    # Ohne diesen Zweig ist ein 502 nur noch im Rueckgabewert
                    # sichtbar — vorher lief er in den generischen Handler.
                    logger.error(
                        "scrape.price_failed", scraper=scraper_cls.__name__, status=exc.response.status_code
                    )
            except Exception as exc:
                results["errors"].append(f"{scraper_cls.__name__}: {exc}")
                logger.error("scrape.price_failed", scraper=scraper_cls.__name__, error=str(exc))

        for scraper_cls in METADATA_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    info = await scraper.get_set_info(set_number)
                    if info:
                        _apply_set_info(lego_set, info, overwrite_uvp=True)
            except httpx.HTTPStatusError as exc:
                results["errors"].append(f"{scraper_cls.__name__} metadata: HTTP {exc.response.status_code}")
                if exc.response.status_code in (403, 429):
                    results["blocked"] = True
                    logger.warning(
                        "scrape.metadata_blocked", scraper=scraper_cls.__name__, status=exc.response.status_code
                    )
                else:
                    logger.error(
                        "scrape.metadata_failed", scraper=scraper_cls.__name__, status=exc.response.status_code
                    )
            except Exception as exc:
                results["errors"].append(f"{scraper_cls.__name__} metadata: {exc}")
                logger.error("scrape.metadata_failed", scraper=scraper_cls.__name__, error=str(exc))

        for scraper_cls in OFFER_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    # Bewusst ohne _enrich_offer_details: KleinanzeigenScraper
                    # steht auch in OFFER_SCRAPERS, und die 2h-Lane holt
                    # dieselben Detailseiten. Beide anzureichern verdreifachte
                    # den Fussabdruck, ohne eine einzige Zusatzinfo zu liefern.
                    offers = await scraper.get_offers(set_number)
                results["offers"] += await _upsert_offers(session, lego_set, offers, now)
            except httpx.HTTPStatusError as exc:
                results["errors"].append(f"{scraper_cls.__name__} offers: HTTP {exc.response.status_code}")
                if exc.response.status_code in (403, 429):
                    # Ohne dieses Flag verschluckt das generische except den
                    # Rate-Limit-Hinweis, und der Watchlist-Lauf haemmert
                    # ueber alle restlichen Sets weiter.
                    results["blocked"] = True
                    logger.warning(
                        "scrape.offers_blocked",
                        scraper=scraper_cls.__name__,
                        status=exc.response.status_code,
                    )
                else:
                    logger.error(
                        "scrape.offers_failed",
                        scraper=scraper_cls.__name__,
                        status=exc.response.status_code,
                    )
            except Exception as exc:
                results["errors"].append(f"{scraper_cls.__name__} offers: {exc}")
                logger.error("scrape.offers_failed", scraper=scraper_cls.__name__, error=str(exc))

        if scraped_prices:
            consensus = calculate_consensus(scraped_prices)
            # Gespeichert wird nur ein Konsens, der von mehr als einer Quelle
            # getragen wird und deren Spanne nicht auseinanderlaeuft. Auf
            # is_reliable zu sperren war zu grob: der EBAY_ACTIVE-Fallback
            # markiert jeden Konsens als unsicher, wodurch ueberhaupt kein
            # Marktpreis mehr geschrieben wurde.
            if consensus.consensus_price > 0 and _is_persistable(consensus):
                lego_set.current_market_price = consensus.consensus_price
                lego_set.market_price_updated_at = now
            elif consensus.consensus_price > 0:
                logger.info(
                    "scrape.consensus_unreliable",
                    set_number=set_number,
                    sources=consensus.num_sources,
                    price=consensus.consensus_price,
                )

        await session.commit()

    logger.info("scrape.complete", **results)
    return results


@celery_app.task(name="app.tasks.scrape_daily.scrape_all_watched_sets")
def scrape_all_watched_sets() -> dict:
    """Scrape all sets on the watchlist."""
    return _run_async(_scrape_all_watched_async())


async def _scrape_all_watched_async() -> dict:
    """Async implementation of full watchlist scraping."""
    summary = {"total_sets": 0, "success": 0, "errors": 0, "aborted": False}

    async with async_session() as session:
        result = await session.execute(
            select(LegoSet.set_number)
            .join(WatchlistItem, WatchlistItem.set_id == LegoSet.id)
            .where(WatchlistItem.is_active)
        )
        set_numbers = [row[0] for row in result.all()]
        summary["total_sets"] = len(set_numbers)

    for set_number in set_numbers:
        try:
            result = await _scrape_set_prices_async(set_number)
            if result["errors"]:
                summary["errors"] += 1
            else:
                summary["success"] += 1
            if result.get("blocked"):
                summary["aborted"] = True
                logger.warning("scrape.all_aborted_blocked", set_number=set_number)
                break
        except Exception as exc:
            summary["errors"] += 1
            logger.error("scrape.set_failed", set_number=set_number, error=str(exc))

    logger.info("scrape.all_complete", **summary)
    return summary


async def _enrich_offer_details(scraper, lego_set: LegoSet, offers) -> int:
    """Read condition off the listing page — only for plausible set offers.

    The result list never carries the condition, so it used to be guessed from
    title keywords: "Lego Eisvogel 10331" gave no hint that the set comes
    without box or instructions, and a 10 EUR listing was valued against the
    37,89 EUR a complete one fetches.

    The detail page costs one request per listing, so it is fetched only for
    offers that passed the identity filter and would actually reach the feed —
    and only for the `scraper_detail_max_per_set` cheapest of those. Cheapest
    first because that is where the condition decides GO/NO-GO: a listing far
    under market is either the find or a wreck, and only the detail page tells
    them apart. Scrapers without the capability are skipped.
    """
    if not hasattr(scraper, "fetch_offer_details"):
        return 0

    reference_price = getattr(lego_set, "current_market_price", None) or getattr(lego_set, "uvp_eur", None)
    candidates = [
        offer
        for offer in offers
        if offer.offer_url
        and is_set_offer(
            offer.offer_title,
            lego_set.set_number,
            price_eur=offer.price_eur,
            reference_price=reference_price,
            set_name=getattr(lego_set, "set_name", None),
        )
    ]
    # Ein fehlender Preis ist unbekannt, nicht teuer — er gehoert nach vorne,
    # statt hinten aus dem Budget zu fallen.
    candidates.sort(key=lambda o: (o.price_eur is not None, o.price_eur or 0.0))

    cap = settings.scraper_detail_max_per_set
    if 0 < cap < len(candidates):
        # Kein stiller Schnitt: was hier wegfaellt, steht im Log. Ein Deckel,
        # den niemand sieht, liest sich spaeter wie vollstaendige Abdeckung.
        logger.info(
            "scrape.details_capped",
            set_number=lego_set.set_number,
            scraper=type(scraper).__name__,
            candidates=len(candidates),
            cap=cap,
        )
        candidates = candidates[:cap]

    enriched = 0
    for offer in candidates:
        details = await scraper.fetch_offer_details(offer.offer_url)
        if not details:
            continue

        offer.condition = details.condition
        offer.box_damage = details.box_damage
        offer.sealed = details.condition == "NEW_SEALED"
        offer.details_verified = True
        enriched += 1

    return enriched


async def _upsert_offers(session, lego_set: LegoSet, offers, now: datetime) -> int:
    """Insert new offers / refresh known ones, keyed by (platform, canonical URL).

    Every stored offer of the set is loaded, not just the URLs seen in this run:
    rows written before URL canonicalisation still carry their tracking tokens,
    and matching them on the canonical form heals them instead of adding yet
    another copy of the same listing.
    """
    existing_offer_result = await session.execute(select(Offer).where(Offer.set_id == lego_set.id))
    existing_by_key: dict[tuple[str, str], Offer] = {}
    for existing in existing_offer_result.scalars().all():
        existing_by_key.setdefault((existing.platform, canonical_offer_url(existing.offer_url)), existing)

    reference_price = getattr(lego_set, "current_market_price", None) or getattr(lego_set, "uvp_eur", None)

    count = 0
    for offer in offers:
        # Ohne URL gibt es keinen stabilen Upsert-Key — solche Zeilen würden
        # jede Runde neu inseriert und nie wieder aktualisiert.
        offer_url = canonical_offer_url(offer.offer_url)
        if not offer_url:
            continue

        # Zubehör trägt die Setnummer im Titel und würde sonst gegen den
        # Setpreis bewertet (9,99-EUR-Wandhalterung -> 869 % Phantom-ROI).
        if not is_set_offer(
            offer.offer_title,
            lego_set.set_number,
            price_eur=offer.price_eur,
            reference_price=reference_price,
            set_name=getattr(lego_set, "set_name", None),
        ):
            logger.info(
                "scrape.offer_rejected_not_set",
                set_number=lego_set.set_number,
                title=(offer.offer_title or "")[:80],
                price=offer.price_eur,
            )
            continue
        existing_offer = existing_by_key.get((offer.platform, offer_url))

        if existing_offer:
            # Rewrites a legacy tracking URL to its canonical form on first touch.
            existing_offer.offer_url = offer_url
            existing_offer.offer_title = offer.offer_title
            existing_offer.price_eur = offer.price_eur
            existing_offer.shipping_eur = offer.shipping_eur
            existing_offer.total_price_eur = offer.price_eur + (offer.shipping_eur or 0)
            # Nur ein gelesener Zustand darf einen gelesenen ersetzen. Faellt ein
            # Angebot beim naechsten Lauf aus dem Detail-Cap, liefert die Liste
            # wieder "UNKNOWN" — das duerfte sonst ein geprueftes
            # USED_INCOMPLETE ueberschreiben und den erwarteten Erloes um 40 %
            # nach oben luegen.
            if getattr(offer, "details_verified", False):
                existing_offer.condition = offer.condition
                existing_offer.box_damage = offer.box_damage
                existing_offer.sealed = offer.sealed
            existing_offer.seller_name = offer.seller_name
            existing_offer.seller_rating = offer.seller_rating
            existing_offer.seller_location = offer.seller_location
            existing_offer.status = "ACTIVE"
            existing_offer.last_seen_at = now
            existing_offer.is_auction = offer.is_auction
            existing_offer.auction_end = offer.auction_end
        else:
            new_offer = Offer(
                set_id=lego_set.id,
                platform=offer.platform,
                offer_url=offer_url,
                offer_title=offer.offer_title,
                price_eur=offer.price_eur,
                shipping_eur=offer.shipping_eur,
                total_price_eur=offer.price_eur + (offer.shipping_eur or 0),
                condition=offer.condition,
                box_damage=offer.box_damage,
                sealed=offer.sealed,
                seller_name=offer.seller_name,
                seller_rating=offer.seller_rating,
                seller_location=offer.seller_location,
                status="ACTIVE",
                discovered_at=now,
                last_seen_at=now,
                is_auction=offer.is_auction,
                auction_end=offer.auction_end,
            )
            session.add(new_offer)
            # Derselbe Artikel kann in einer Trefferliste mehrfach auftauchen —
            # ohne diesen Eintrag legte die zweite Fundstelle eine zweite Zeile an.
            existing_by_key[(offer.platform, offer_url)] = new_offer

        count += 1
    return count


@celery_app.task(name="app.tasks.scrape_daily.scrape_kleinanzeigen_watched")
def scrape_kleinanzeigen_watched() -> dict:
    """Fast lane: refresh Kleinanzeigen offers for the watchlist every 2 hours."""
    return _run_async(_scrape_kleinanzeigen_async())


async def _scrape_kleinanzeigen_async() -> dict:
    """Kleinanzeigen-only offer refresh over the active watchlist.

    A 403/429 aborts the remaining run — a rate-limit signal must reduce
    pressure immediately, never turn into a request burst.
    """
    results = {"total_sets": 0, "offers": 0, "errors": [], "aborted": False}

    async with async_session() as session:
        watched = await session.execute(
            select(LegoSet)
            .join(WatchlistItem, WatchlistItem.set_id == LegoSet.id)
            .where(WatchlistItem.is_active)
        )
        lego_sets = list(watched.scalars().all())
        results["total_sets"] = len(lego_sets)

        now = datetime.now(UTC)
        for lego_set in lego_sets:
            blocked_during_enrich = False
            try:
                async with KleinanzeigenScraper() as scraper:
                    offers = await scraper.get_offers(lego_set.set_number)
                    try:
                        await _enrich_offer_details(scraper, lego_set, offers)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code not in (403, 429):
                            raise
                        # Die Trefferliste steht schon — sie faellt nicht mit
                        # dem Block zusammen. Erst speichern, dann aufhoeren.
                        blocked_during_enrich = True
                        results["errors"].append(
                            f"{lego_set.set_number}: HTTP {exc.response.status_code} (Anreicherung)"
                        )
                        logger.warning(
                            "scrape.kleinanzeigen_blocked",
                            set_number=lego_set.set_number,
                            status=exc.response.status_code,
                            stage="enrich",
                        )
                results["offers"] += await _upsert_offers(session, lego_set, offers, now)
                # Pro Set committen: bei 11 Requests je Set reisst der Lauf ab
                # ~14 Sets ins Celery-Softlimit (540 s), und ein Commit erst am
                # Ende haette dann den ganzen Durchlauf verworfen.
                await session.commit()
                if blocked_during_enrich:
                    results["aborted"] = True
                    break
            except httpx.HTTPStatusError as exc:
                results["errors"].append(f"{lego_set.set_number}: HTTP {exc.response.status_code}")
                if exc.response.status_code in (403, 429):
                    results["aborted"] = True
                    logger.warning(
                        "scrape.kleinanzeigen_blocked",
                        set_number=lego_set.set_number,
                        status=exc.response.status_code,
                        stage="list",
                    )
                    break
            except Exception as exc:
                results["errors"].append(f"{lego_set.set_number}: {exc}")
        await session.commit()

    logger.info(
        "scrape.kleinanzeigen_complete",
        total_sets=results["total_sets"],
        offers=results["offers"],
        aborted=results["aborted"],
        errors=len(results["errors"]),
    )
    return results


@celery_app.task(name="app.tasks.scrape_daily.refresh_known_set_metadata")
def refresh_known_set_metadata() -> dict:
    """Refresh UVP/EOL metadata for all known sets once per day."""
    return _run_async(_refresh_known_set_metadata_async())


async def _refresh_known_set_metadata_async() -> dict:
    summary = {"total_sets": 0, "updated": 0, "errors": 0}

    async with async_session() as session:
        result = await session.execute(select(LegoSet).order_by(LegoSet.updated_at.desc()))
        sets = result.scalars().all()
        summary["total_sets"] = len(sets)

        for lego_set in sets:
            try:
                changed = False
                for scraper_cls in METADATA_SCRAPERS:
                    try:
                        async with scraper_cls() as scraper:
                            info = await scraper.get_set_info(lego_set.set_number)
                            if info:
                                changed = _apply_set_info(lego_set, info, overwrite_uvp=True) or changed
                    except Exception as exc:
                        logger.error(
                            "scrape.metadata_refresh_failed",
                            set_number=lego_set.set_number,
                            scraper=scraper_cls.__name__,
                            error=str(exc),
                        )

                if changed:
                    summary["updated"] += 1
            except Exception as exc:
                summary["errors"] += 1
                logger.error("scrape.metadata_set_failed", set_number=lego_set.set_number, error=str(exc))

        await session.commit()

    logger.info("scrape.metadata_refresh_complete", **summary)
    return summary
