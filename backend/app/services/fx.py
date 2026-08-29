"""USD->EUR-Kurs aus dem EZB-Tagesreferenzfeed.

BrickEconomy quotiert in Dollar. Der Umrechnungskurs stand bisher als
Konstante im Scraper — ohne Datum, also ohne Chance, die Drift zu bemerken.

Der Kurs wird hoechstens einmal am Tag geholt und in `app_settings`
zwischengelagert. Faellt der Abruf aus, gilt der letzte bekannte Kurs; gibt
es auch den nicht, greift ein Ersatzwert — und der traegt einen Vermerk, der
bis ins Lauf-Protokoll durchschlaegt.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree

import httpx
import structlog
from sqlalchemy import select

from app.models.base import async_session
from app.models.settings import AppSetting
from app.security.url_policy import validate_marketplace_url

logger = structlog.get_logger()

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
RATE_KEY = "fx_usd_eur"
UPDATED_KEY = "fx_usd_eur_updated_at"
MAX_AGE = timedelta(hours=24)

# Stand 2026-03, uebernommen aus der bisherigen Konstante im BrickEconomy-
# Scraper. Nur Rueckfall, nie stiller Normalfall.
FALLBACK_USD_TO_EUR = 0.92


@dataclass(frozen=True)
class FxRate:
    usd_to_eur: float
    as_of: datetime | None
    is_fallback: bool

    @property
    def note(self) -> str | None:
        """Vermerk fuer das Lauf-Protokoll — None, wenn der Kurs gemessen ist."""
        if not self.is_fallback:
            return None
        return "Ersatzkurs — kein EZB-Kurs verfuegbar"


def parse_ecb_rate(xml_text: str) -> float | None:
    """USD->EUR aus dem EZB-Feed.

    Der Feed quotiert EUR->USD ('rate' sind Dollar je Euro); gebraucht wird
    der Kehrwert.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None

    for node in root.iter():
        if node.get("currency") != "USD":
            continue
        try:
            eur_to_usd = float(node.get("rate", ""))
        except ValueError:
            return None
        if eur_to_usd <= 0:
            return None
        return 1 / eur_to_usd
    return None


def is_fresh(as_of: datetime | None, now: datetime) -> bool:
    """Ob ein zwischengelagerter Kurs noch gilt."""
    if as_of is None:
        return False
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    return (now - as_of) <= MAX_AGE


async def _load_cached() -> tuple[float | None, datetime | None]:
    async with async_session() as session:
        result = await session.execute(
            select(AppSetting).where(AppSetting.key.in_([RATE_KEY, UPDATED_KEY]))
        )
        stored = {s.key: s.value for s in result.scalars()}
    try:
        rate = float(stored[RATE_KEY]) if stored.get(RATE_KEY) else None
    except ValueError:
        rate = None
    try:
        as_of = datetime.fromisoformat(stored[UPDATED_KEY]) if stored.get(UPDATED_KEY) else None
    except ValueError:
        as_of = None
    return rate, as_of


async def _store(rate: float, as_of: datetime) -> None:
    """Kurs ablegen. Ein Fehler hier darf keinen Bewertungslauf kosten."""
    values = {RATE_KEY: f"{rate:.6f}", UPDATED_KEY: as_of.isoformat()}
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AppSetting).where(AppSetting.key.in_(list(values)))
            )
            existing = {s.key: s for s in result.scalars()}
            for key, value in values.items():
                if key in existing:
                    existing[key].value = value
                else:
                    session.add(
                        AppSetting(key=key, value=value, category="fx", label="USD/EUR-Kurs (EZB)")
                    )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — Zwischenlager darf nie den Lauf brechen
        logger.warning("fx.store_failed", error=str(exc))


async def _fetch_ecb() -> float | None:
    url = validate_marketplace_url(ECB_DAILY_URL, "ECB")
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return parse_ecb_rate(response.text)


async def get_usd_to_eur() -> FxRate:
    """Tageskurs — frisch, sonst zwischengelagert, sonst Ersatzwert."""
    now = datetime.now(UTC)
    cached_rate, cached_at = await _load_cached()
    if cached_rate is not None and is_fresh(cached_at, now):
        return FxRate(usd_to_eur=cached_rate, as_of=cached_at, is_fallback=False)

    try:
        fetched = await _fetch_ecb()
    except Exception as exc:  # noqa: BLE001 — jeder Ausfall faellt auf den Cache zurueck
        logger.warning("fx.fetch_failed", error=str(exc))
        fetched = None

    if fetched is not None:
        await _store(fetched, now)
        return FxRate(usd_to_eur=fetched, as_of=now, is_fallback=False)

    if cached_rate is not None:
        logger.warning("fx.using_stale_rate", as_of=cached_at.isoformat() if cached_at else None)
        return FxRate(usd_to_eur=cached_rate, as_of=cached_at, is_fallback=False)

    logger.warning("fx.using_fallback_rate", rate=FALLBACK_USD_TO_EUR)
    return FxRate(usd_to_eur=FALLBACK_USD_TO_EUR, as_of=None, is_fallback=True)
