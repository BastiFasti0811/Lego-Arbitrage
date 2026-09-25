"""Helpers for scanning Catawiki category pages and lot pages.

Grundlage sind echte Seiten vom 25.09.2026 (tests/fixtures/catawiki_*):
Catawiki ist eine Next.js-App. Los- und Listendaten stehen strukturiert in
`__NEXT_DATA__`; Versand und Kaeuferschutzgebuehr laedt die Seite erst im
Browser nach und stehen deshalb nicht im HTML, sondern kommen aus zwei
JSON-Endpunkten. Der Fliesstext der Seite taugt fuer keine dieser Angaben:
er enthaelt KI-Zusammenfassungen, "Verkauft von <Shop>" und Empfehlungs-Lose.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper
from app.security.url_policy import validate_url_for_scraper

logger = structlog.get_logger()

CATAWIKI_BASE = "https://www.catawiki.com"
# Akamai vor catawiki.com beantwortet Los-Seiten mit 403, wenn der User-Agent
# zufaellig rotiert (fake-useragent); ein fester aktueller Chrome kommt durch
# (geprueft am 25.09.2026). Die JSON-Endpunkte waren in beiden Faellen offen.
# Der Wert altert mit jedem Chrome-Release; ueberschreibbar per Einstellung
# `catawiki_user_agent`, falls Akamai ihn irgendwann nicht mehr durchlaesst.
CATAWIKI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0.0.0 Safari/537.36"
)
SHIPPING_DESTINATION = "de"
# Bei JEDER Aenderung an Parser oder Zustandslogik hochzaehlen. Prod nimmt
# Heimrechner-Ergebnisse nur mit derselben Version an: ein veralteter Checkout
# auf dem PC darf keine Lose nach altem Regelwerk als versiegelt melden.
PARSER_VERSION = "2026-09-25.3"


@dataclass
class CatawikiLotCandidate:
    lot_id: str
    title: str
    url: str
    current_bid: float | None = None
    shipping_eur: float | None = None
    seller_location: str | None = None
    auction_end_label: str | None = None
    set_numbers: list[str] | None = None
    condition: str = "UNKNOWN"
    box_damage: bool = False
    is_closed: bool = False
    details_verified: bool = False
    buyer_fee_rate: float | None = None
    buyer_fee_fixed: float | None = None


class CatawikiParseError(ValueError):
    """The response could not be identified as a Catawiki lot/result page."""


def canonical_lot_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname not in {"www.catawiki.com", "catawiki.com"}:
        raise CatawikiParseError("Kein Catawiki-Loslink")
    match = re.search(r"/l/(\d+)(?:[-/]|$)", parts.path)
    if not match:
        raise CatawikiParseError("Catawiki-Losnummer fehlt")
    return f"{CATAWIKI_BASE}/de/l/{match.group(1)}"


def _extract_set_numbers(text: str) -> list[str]:
    numbers = []
    for match in re.finditer(r"\b(\d{4,6})(?:-1)?\b", text or ""):
        number = match.group(1)
        if 1900 <= int(number) <= 2099:
            continue
        if re.match(r"\s*(?:Teile|pieces|pcs|EUR|€)\b", text[match.end():], re.IGNORECASE):
            continue
        numbers.append(number)
    return list(dict.fromkeys(numbers))


def _extract_next_json(html: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    script = soup.select_one("script#__NEXT_DATA__")
    if not script or not script.string:
        return None
    try:
        return json.loads(script.string)
    except json.JSONDecodeError:
        return None


def _page_props(html: str) -> dict | None:
    payload = _extract_next_json(html)
    props = (payload or {}).get("props", {}).get("pageProps")
    return props if isinstance(props, dict) else None


# Zustand und Verpackung sind bei Catawiki feste Katalogwerte mit ID (Filter
# 914 bzw. 900), kein Freitext. Werte und IDs aus 45 echten Losen vom
# 25.09.2026. NEW_SEALED gibt es NUR ueber diese Positivliste: ein Textmuster
# kann nie hochstufen ("nicht mehr ganz versiegelt", "Re-sealed", "Siegel
# eingerissen" waren mit Regex sonst NEW_SEALED). Unbekannte Werte -> UNKNOWN,
# und das sperrt jede Freigabe.
ZUSTAND_UNUSED_ID = 165212  # "Unbenutzt"
ZUSTAND_USED_IDS = frozenset({
    62422,  # "Gebraucht"
    67508,  # "Viel gebraucht"
    63877,  # "Neuwertig" -- meist gebraucht, nie neu
    62421,  # "Gut"
})
ZUSTAND_ID_BY_TEXT = {
    "Unbenutzt": 165212, "Gebraucht": 62422, "Viel gebraucht": 67508, "Neuwertig": 63877, "Gut": 62421,
}
# Verpackung bei Zustand "Unbenutzt": (Zustand, Kartonschaden).
VERPACKUNG_CONDITION = {
    80575: ("NEW_SEALED", False),    # "In unbeschädigter und versiegelter Originalverpackung"
    # Belegt an Los 107053796 (21368 Peanuts, 25.09.2026). "Ungeoeffnet" ist nicht
    # woertlich "versiegelt": bewusst NEW_SEALED mit Kartonschaden-Abschlag.
    80577: ("NEW_SEALED", True),     # "In beschädigter ungeöffneter Originalverpackung"
    92699: ("NEW_OPEN_BOX", False),  # "ungeöffnete Schachtel Dichtungen defekt"
    80579: ("NEW_OPEN_BOX", False),  # "In unbeschädigter geöffneter Originalverpackung"
    92711: ("NEW_OPEN_BOX", False),  # "mit Handbuch in geöffneter Box"
    80571: ("NEW_OPEN_BOX", False),  # "Ausgepackt"
    # Bewusst UNKNOWN (sagen nichts ueber das Siegel): 92701 "in geschlossener Box",
    # 93427 "Mit Original-Kasten", 92715 "mit Handbuch", 70592 "In Ersatzverpackung",
    # 64305 "Ohne Originalverpackung".
}
VERPACKUNG_ID_BY_TEXT = {
    "In unbeschädigter und versiegelter Originalverpackung": 80575,
    "In beschädigter ungeöffneter Originalverpackung": 80577,
    "ungeöffnete Schachtel Dichtungen defekt": 92699,
    "In unbeschädigter geöffneter Originalverpackung": 80579,
    "mit Handbuch in geöffneter Box": 92711,
    "Ausgepackt": 80571,
    "in geschlossener Box": 92701,
    "Mit Original-Kasten": 93427,
    "mit Handbuch": 92715,
    "In Ersatzverpackung": 70592,
    "Ohne Originalverpackung": 64305,
}
_NO_MINIFIGS = re.compile(r"\b(?:no|keine?|ohne|without)\s+(?:mini-?)?fig", re.IGNORECASE)


def condition_from_catawiki(
    zustand: str | None,
    verpackung: str | None,
    complete: str | None = None,
    title: str = "",
    zustand_id: int | None = None,
    verpackung_id: int | None = None,
) -> tuple[str, bool]:
    """Zustand und Kartonschaden aus den Catawiki-Katalogangaben.

    Massgeblich sind die Katalog-IDs; ohne ID (Losliste, Heimrechner-Text) der
    exakte Katalogtext. Fehlende Minifiguren oder "Vollstaendiges Set: Nein"
    machen jedes Los unvollstaendig.
    """
    if (complete or "").strip().lower() == "nein" or _NO_MINIFIGS.search(title or ""):
        return "USED_INCOMPLETE", False
    zustand_id = zustand_id if isinstance(zustand_id, int) else ZUSTAND_ID_BY_TEXT.get((zustand or "").strip())
    if zustand_id in ZUSTAND_USED_IDS:
        return "USED_COMPLETE", False
    if zustand_id != ZUSTAND_UNUSED_ID:
        return "UNKNOWN", False
    if not isinstance(verpackung_id, int):
        verpackung_id = VERPACKUNG_ID_BY_TEXT.get((verpackung or "").strip())
    return VERPACKUNG_CONDITION.get(verpackung_id, ("UNKNOWN", False))


def _condition_from_subtitle(subtitle: str | None, title: str) -> tuple[str, bool]:
    # Loslisten zeigen "Unbenutzt - In unbeschaedigter und versiegelter Originalverpackung".
    zustand, _, verpackung = (subtitle or "").partition(" - ")
    return condition_from_catawiki(zustand, verpackung, None, title)


def _candidates_from_list(lots: list) -> list[CatawikiLotCandidate]:
    candidates: list[CatawikiLotCandidate] = []
    seen: set[str] = set()
    for node in lots:
        if not isinstance(node, dict):
            continue
        title, lot_id = node.get("title"), str(node.get("id", ""))
        if not isinstance(title, str) or "lego" not in title.lower() or not lot_id.isdigit() or lot_id in seen:
            continue
        condition, box_damage = _condition_from_subtitle(node.get("subtitle"), title)
        candidates.append(CatawikiLotCandidate(
            lot_id=lot_id, title=title, url=f"{CATAWIKI_BASE}/de/l/{lot_id}",
            set_numbers=_extract_set_numbers(title), condition=condition, box_damage=box_damage,
        ))
        seen.add(lot_id)
    return candidates


def parse_category_page(html: str, source_url: str) -> list[CatawikiLotCandidate]:
    """Lose einer Catawiki-Auktionsseite (/a/) oder Kategorieseite (/c/).

    Quelle ist die Losliste in __NEXT_DATA__ ("lots" bzw. "categoryLots.lots"),
    nie "similarLots" oder Navigationslinks. Gebote stehen dort nicht, die
    liest get_lot je Los nach.
    """
    props = _page_props(html)
    if props is not None:
        lots = props.get("lots")
        if not isinstance(lots, list):
            lots = (props.get("categoryLots") or {}).get("lots")
        if isinstance(lots, list):
            return _candidates_from_list(lots)

    # Fallback ohne __NEXT_DATA__: nur Loslinks, ohne Gebot und Zustand.
    candidates: list[CatawikiLotCandidate] = []
    seen: set[str] = set()
    for anchor in BeautifulSoup(html, "lxml").select("a[href*='/l/']"):
        try:
            full_url = canonical_lot_url(urljoin(source_url, anchor.get("href") or ""))
        except CatawikiParseError:
            continue
        title = anchor.get_text(" ", strip=True)
        if full_url in seen or not title or "lego" not in title.lower():
            continue
        candidates.append(CatawikiLotCandidate(
            lot_id=full_url.rsplit("/", 1)[-1], title=title, url=full_url,
            set_numbers=_extract_set_numbers(title),
        ))
        seen.add(full_url)
    return candidates


def needs_lot_details(lot: CatawikiLotCandidate) -> bool:
    """Vorfilter aus der Losliste: Details nur fuer moegliche Einzelsets in OVP laden.

    Jedes Los kostet drei Abrufe mit Pause. Was laut Liste gebraucht, geoeffnet
    oder ein Sammelposten ist, wird ohnehin nie freigegeben.
    """
    from app.domain.identity import is_set_offer

    if not lot.set_numbers or len(lot.set_numbers) != 1:
        return False
    if not is_set_offer(lot.title, lot.set_numbers[0]):
        return False
    return lot.condition in {"NEW_SEALED", "UNKNOWN"}


def lot_review_reasons(lot: CatawikiLotCandidate) -> list[str]:
    from app.domain.identity import is_set_offer

    reasons = []
    if lot.is_closed:
        reasons.append("Auktion beendet.")
    if not lot.details_verified:
        reasons.append("Losdetails nicht geladen oder nicht lesbar.")
    if not lot.set_numbers or len(lot.set_numbers) != 1:
        reasons.append("Set-Zuordnung unklar oder mehrere Sets: Posten einzeln pruefen.")
    elif not is_set_offer(lot.title, lot.set_numbers[0]):
        reasons.append("Zubehoer oder Sammelangebot: keine Einzelset-Bewertung.")
    if lot.current_bid is None:
        reasons.append("Aktuelles EUR-Gebot nicht lesbar.")
    if lot.shipping_eur is None:
        reasons.append("Versand nach Deutschland fehlt: Kosten am Los pruefen.")
    if lot.condition != "NEW_SEALED":
        reasons.append("Nicht als neu und versiegelt bestaetigt: Zustand manuell pruefen.")
    return reasons


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _eur_amount(value) -> float | None:
    """Nur die ausdruecklich waehrungsbezogene Form {"EUR": ...}, nie ein nackter Skalar."""
    return _number(value.get("EUR")) if isinstance(value, dict) else None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def parse_lot_page(html: str, url: str) -> CatawikiLotCandidate:
    """Los-Details aus __NEXT_DATA__ einer Catawiki-Losseite.

    Gebot, Status und Zustand kommen nur aus den strukturierten Daten des Loses
    (lotDetailsData, biddingBlockResponse). Versand und Gebuehr stehen nicht im
    HTML; die holt CatawikiScraper.get_lot nach. Ohne passende __NEXT_DATA__
    bleibt das Los unverifiziert und damit gesperrt.
    """
    url = canonical_lot_url(url)
    lot_id = url.rsplit("/", 1)[-1]
    props = _page_props(html) or {}
    details = props.get("lotDetailsData")
    specs_raw = details.get("specifications") if isinstance(details, dict) else None
    if (
        not isinstance(details, dict)
        or str(details.get("lotId")) != lot_id
        or not isinstance(specs_raw, (list, type(None)))
    ):
        # Kein oder fremdes Los, oder die Datenform hat sich geaendert: unverifiziert.
        title_el = BeautifulSoup(html, "lxml").select_one("h1")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:
            raise CatawikiParseError("Catawiki-Losseite nicht lesbar (Titel fehlt)")
        return CatawikiLotCandidate(lot_id=lot_id, title=title, url=url, set_numbers=_extract_set_numbers(title))

    title = details.get("lotTitle") or ""
    specs = {
        spec.get("name"): spec
        for spec in specs_raw or []
        if isinstance(spec, dict) and isinstance(spec.get("value"), (str, type(None)))
    }

    def spec(name: str, field: str = "value"):
        return (specs.get(name) or {}).get(field)

    condition, box_damage = condition_from_catawiki(
        spec("Zustand"), spec("Verpackung"), spec("Vollständiges Set"), title,
        zustand_id=spec("Zustand", "valueId"), verpackung_id=spec("Verpackung", "valueId"),
    )

    bidding = _dict(props.get("biddingBlockResponse"))
    live_lot = _dict(_dict(bidding.get("live")).get("lot"))
    in_eur = _dict(props.get("userData")).get("currencyCode") == "EUR"
    current_bid = _eur_amount(live_lot.get("bid"))
    if current_bid is None and in_eur:
        current_bid = _number(bidding.get("localizedCurrentBidAmount"))
    if not current_bid:
        # Ohne Gebot steht dort 0; zu zahlen ist dann mindestens der Startpreis.
        current_bid = _number(bidding.get("localizedStartBidAmount")) if in_eur else None
    closed = bool(
        details.get("isClosed") or bidding.get("closed") or bidding.get("sold")
        or live_lot.get("closeStatus") not in (None, "Open")
    )

    set_numbers = _extract_set_numbers(title)
    serial = str(spec("Seriennummer") or "").strip()
    if serial.isdigit() and set_numbers != [serial]:
        # Titel und Detailangabe widersprechen sich: pruefen statt raten.
        set_numbers = list(dict.fromkeys([*set_numbers, serial]))

    return CatawikiLotCandidate(
        lot_id=lot_id,
        title=title,
        url=url,
        current_bid=current_bid,
        set_numbers=set_numbers,
        condition=condition,
        box_damage=box_damage,
        is_closed=closed,
        details_verified=True,
    )


def parse_shipping_rates(payload: dict, destination: str = SHIPPING_DESTINATION) -> float | None:
    """Versand nach `destination` aus /buyer/api/v2/lots/<id>/shipping (Preise in Cent).

    Mehrere Tarife nach DE: der guenstigste, so wie ihn Catawiki anzeigt.
    """
    rates = _dict(_dict(payload).get("shipping")).get("rates")
    prices = [
        _number(rate.get("price"))
        for rate in (rates if isinstance(rates, list) else [])
        if isinstance(rate, dict) and rate.get("region_code") == destination and rate.get("currency_code") == "EUR"
    ]
    prices = [price for price in prices if price is not None]
    return round(min(prices) / 100, 2) if prices else None


def parse_commission(payload: dict) -> tuple[float | None, float | None]:
    """Kaeuferschutzgebuehr aus /fees/api/v1/buyer/lots/<id>/commission: (Anteil, fix in EUR)."""
    payload = _dict(payload)
    if payload.get("currency_code") != "EUR":
        return None, None
    percentage = _number(_dict(payload.get("variable")).get("percentage"))
    fixed = _number(_dict(payload.get("fixed")).get("amount"))
    rate = percentage / 100 if percentage is not None else None
    fixed_eur = round(fixed / 100, 2) if fixed is not None else None
    return rate, fixed_eur


class CatawikiScraper(BaseScraper):
    """Minimal Catawiki scraper that works with optional manual cookies."""

    def __init__(self, cookie_header: str | None = None, user_agent: str | None = None):
        super().__init__()
        self.cookie_header = cookie_header
        # BaseScraper._fetch rotiert sonst je Abruf einen Zufalls-UA, darauf antwortet Akamai mit 403.
        self.user_agent_override = user_agent or CATAWIKI_USER_AGENT

    async def _get_client(self):
        client = await super()._get_client()
        if self.cookie_header:
            client.headers["Cookie"] = self.cookie_header
        client.headers["User-Agent"] = self.user_agent_override
        client.headers["Referer"] = CATAWIKI_BASE
        return client

    async def get_set_info(self, set_number: str):
        return None

    async def get_price(self, set_number: str):
        return None

    async def scan_category(self, category_url: str, limit: int = 30) -> list[CatawikiLotCandidate]:
        html = await self._fetch(category_url)
        lots = parse_category_page(html, category_url)[:limit]
        if not lots and not re.search(r"Keine (?:Ergebnisse|Lose)|No (?:results|lots)", html, re.IGNORECASE):
            raise CatawikiParseError("Keine Catawiki-Lose lesbar: Seite oder Zugriff pruefen")
        return lots

    async def _fetch_json(self, url: str) -> dict:
        # Nicht ueber BaseScraper._fetch: dessen looks_undecoded erkennt Text an
        # HTML-Markern und haelt jede JSON-Antwort fuer Binaerdaten.
        safe_url = validate_url_for_scraper(url, self.name)
        await self._delay()
        client = await self._get_client()
        logger.info("scraper.fetch_json", scraper=self.name, url=safe_url[:100])
        response = await client.get(safe_url, headers={"Accept": "application/json"})
        validate_url_for_scraper(str(response.url), self.name)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Catawiki-API lieferte kein JSON-Objekt")
        return payload

    async def get_lot(self, lot_url: str) -> CatawikiLotCandidate:
        lot = parse_lot_page(await self._fetch(lot_url), lot_url)
        if not lot.details_verified:
            return lot
        amount_cents = int(round((lot.current_bid or 0) * 100))
        try:
            lot.shipping_eur = parse_shipping_rates(await self._fetch_json(
                f"{CATAWIKI_BASE}/buyer/api/v2/lots/{lot.lot_id}/shipping"
                f"?locale=de&currency_code=EUR&amount={amount_cents}"
            ))
        except (ValueError, TypeError, AttributeError, httpx.HTTPError) as exc:
            # Fehlender Versand sperrt die Freigabe ueber lot_review_reasons.
            logger.warning("catawiki.shipping_unavailable", lot_id=lot.lot_id, error=type(exc).__name__)
        try:
            lot.buyer_fee_rate, lot.buyer_fee_fixed = parse_commission(await self._fetch_json(
                f"{CATAWIKI_BASE}/fees/api/v1/buyer/lots/{lot.lot_id}/commission?currency_code=EUR"
            ))
        except (ValueError, TypeError, AttributeError, httpx.HTTPError) as exc:
            # Ohne Gebuehr rechnet evaluate_auction mit dem Plattform-Standard.
            logger.warning("catawiki.commission_unavailable", lot_id=lot.lot_id, error=type(exc).__name__)
        return lot
