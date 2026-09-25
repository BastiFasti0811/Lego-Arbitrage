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


# "Verpackung" ist Freitext des Verkaeufers. Deshalb Wortanfaenge statt
# Teilwoerter, und Verneinungen werden vor jeder positiven Pruefung gesperrt:
# "nicht versiegelt", "Unversiegelte OVP", "unsealed" waren sonst NEW_SEALED.
_NOT_A_WORD_BEFORE = r"(?<![a-zäöüß])"
_NOT_SEALED = re.compile(
    r"\b(?:nicht|kein\w*|ohne)\s+(?:\w+\s+)?(?:versiegel|sealed)|unversiegel|\bun-?sealed|\bnot\s+sealed",
    re.IGNORECASE,
)
_UNOPENED = re.compile(_NOT_A_WORD_BEFORE + r"unge(?:ö|oe)ffnet", re.IGNORECASE)
_OPENED = re.compile(r"(?<!un)ge(?:ö|oe)ffnet", re.IGNORECASE)
_SEALED = re.compile(_NOT_A_WORD_BEFORE + r"(?:versiegelt|sealed)", re.IGNORECASE)
_SEAL_BROKEN = re.compile(
    r"(?:Dichtung|Siegel|Versiegelung)\w*\s+(?:\w+\s+)?"
    r"(?:defekt|besch(?:ä|ae)digt|gebrochen|fehl\w*|angerissen|aufgerissen|offen|entfernt|ge(?:ö|oe)ffnet)"
    r"|(?:angerissen|aufgerissen|gebrochen|defekt|besch(?:ä|ae)digt)\w*\s+(?:Dichtung|Siegel|Versiegelung)",
    re.IGNORECASE,
)
_DAMAGED = re.compile(
    r"(?<!un)besch(?:ä|ae)digt|besch(?:ä|ae)digung|dellen?\b|eingedr(?:ü|ue)ckt|knick|\briss|\bdent|crushed|(?<!un)damaged",
    re.IGNORECASE,
)
# Verneinte Schadensangaben ("ohne Beschaedigungen", "keine Dellen") vor der
# Schadenssuche entfernen; "unbeschaedigt" faengt _DAMAGED selbst ab.
_NEGATED_PHRASE = re.compile(r"\b(?:ohne|keine?[nrs]?)\s+\w+", re.IGNORECASE)
_NO_MINIFIGS = re.compile(r"\b(?:no|keine?|ohne|without)\s+(?:mini-?)?fig", re.IGNORECASE)
_NEW_OR_UNUSED = re.compile(r"(?:neu|unbenutzt)(?![a-zäöüß])", re.IGNORECASE)


def condition_from_catawiki(
    zustand: str | None,
    verpackung: str | None,
    complete: str | None = None,
    title: str = "",
) -> tuple[str, bool]:
    """Zustand und Kartonschaden aus den Catawiki-Detailangaben.

    Beobachtete Werte (Auktion 1256209): Zustand "Unbenutzt"/"Gebraucht";
    Verpackung z. B. "In unbeschaedigter und versiegelter Originalverpackung",
    "In beschaedigter ungeoeffneter Originalverpackung", "ungeoeffnete Schachtel
    Dichtungen defekt", "In unbeschaedigter geoeffneter Originalverpackung",
    "in geschlossener Box"; "Vollstaendiges Set" "Ja"/"Nein".
    NEW_SEALED nur bei eindeutiger Angabe; Unklares wird UNKNOWN, und das
    sperrt jede Freigabe.
    """
    zustand = (zustand or "").strip().lower()
    verpackung = verpackung or ""
    if (complete or "").strip().lower() == "nein" or _NO_MINIFIGS.search(title or ""):
        return "USED_INCOMPLETE", False
    if zustand.startswith("gebraucht"):
        return "USED_COMPLETE", False
    # "Neuwertig" ist meist gebraucht: nur "Neu"/"Unbenutzt" als ganzes Wort.
    if not _NEW_OR_UNUSED.match(zustand):
        return "UNKNOWN", False
    damaged = bool(_DAMAGED.search(_NEGATED_PHRASE.sub(" ", verpackung)))
    if _NOT_SEALED.search(verpackung) or _SEAL_BROKEN.search(verpackung) or _OPENED.search(verpackung):
        return "NEW_OPEN_BOX", damaged
    if _SEALED.search(verpackung) or _UNOPENED.search(verpackung):
        return "NEW_SEALED", damaged
    return "UNKNOWN", False


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
        spec.get("name"): spec.get("value")
        for spec in specs_raw or []
        if isinstance(spec, dict) and isinstance(spec.get("value"), (str, type(None)))
    }
    condition, box_damage = condition_from_catawiki(
        specs.get("Zustand"), specs.get("Verpackung"), specs.get("Vollständiges Set"), title,
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
    serial = str(specs.get("Seriennummer") or "").strip()
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
