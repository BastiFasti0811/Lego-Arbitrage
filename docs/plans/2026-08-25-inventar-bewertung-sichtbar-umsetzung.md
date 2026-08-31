# Inventar-Bewertung sichtbar machen — Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Inventar-Bewertung liefert wieder Marktwerte — und sagt bei jedem Set, das keinen bekommt, warum nicht.

**Architecture:** Erst die Ursache (der gemeinsame HTTP-Client fordert eine Kompression an, die das Image nicht entpacken kann), dann die Sichtbarkeit (jeder Lauf schreibt je Set Ergebnis, Grund und Quellenlage in zwei neue Tabellen), dann die Bedienung (Knopf, Protokoll-Seite, Dubletten-Hinweis, Referenz-Links). Backend zuerst, Frontend danach — jede Frontend-Aufgabe hat ihren Endpunkt bereits.

**Tech Stack:** FastAPI, SQLAlchemy 2 (async), Alembic, Celery + Redis, httpx, BeautifulSoup/lxml, pytest + pytest-asyncio, React 19 + react-query + Tailwind, Vite.

**Spec:** [2026-08-25-inventar-bewertung-sichtbar-machen.md](2026-08-25-inventar-bewertung-sichtbar-machen.md)

## Global Constraints

- Python 3.12 (CI-Version; lokal `py -3.12`, venv liegt in `backend/.venv`)
- Tests laufen aus `backend/`: `./.venv/Scripts/python.exe -m pytest`
- Lint: `./.venv/Scripts/python.exe -m ruff check .` muss sauber bleiben
- Frontend aus `frontend/`: `npm run lint` und `npm run build` müssen durchlaufen
- Alembic-Head vor diesem Plan: `b8e5c30d7f14`. Die neue Revision hängt daran.
- Alle Modelle erben von `app.models.base.Base`, die `id`, `created_at`, `updated_at` bereits mitbringt — diese drei Spalten nie erneut deklarieren.
- Kommentare und Docstrings in diesem Projekt erklären **warum**, nicht was. Deutsche Kommentare, wo der bestehende Code deutsch kommentiert.
- Nutzertexte im Frontend sind deutsch.
- Kein Wert wird stillschweigend geraten: Jeder Rückfall auf einen Ersatzwert hinterlässt eine Spur, die bis ins Protokoll durchschlägt.
- Celery-Tasks laufen über `app.tasks.async_runner.run_async`, nie über `asyncio.run` direkt.

---

### Task 1: Der Client behauptet nur, was er entpacken kann

Die Wurzel des Ausfalls. `BaseScraper._get_client()` setzt `Accept-Encoding: gzip, deflate, br` fest, aber im Image fehlt `brotli`. httpx reicht den komprimierten Körper unentpackt durch, `.text` liefert Binärmüll, und jeder Parser dahinter sieht eine leere Seite. BrickEconomy hat deshalb noch nie einen Preis geliefert.

**Files:**
- Modify: `backend/app/scrapers/base.py` (Header in `_get_client`, Wächter in `_fetch`)
- Modify: `backend/app/scrapers/brickeconomy.py` (stumme Rückgabe in `get_price`)
- Modify: `backend/pyproject.toml` (httpx-Extra)
- Test: `backend/tests/test_scraper_encoding.py` (neu)

**Interfaces:**
- Consumes: nichts
- Produces: `app.scrapers.base.UndecodableResponseError` (Exception), `app.scrapers.base.looks_undecoded(body: str) -> bool`

- [ ] **Step 1: Write the failing test**

Neue Datei `backend/tests/test_scraper_encoding.py`:

```python
"""Der gemeinsame Client darf keine Kompression anfordern, die er nicht entpacken kann.

BrickEconomy antwortete fuenf Monate lang mit HTTP 200 und einem Koerper, den
niemand lesen konnte: der Client verlangte brotli, das Image hatte keinen
Decoder, httpx reichte die Rohbytes durch. `_search_set` fand null Treffer und
`get_price` stieg ohne Logzeile aus.
"""

import httpx
import pytest

from app.scrapers.base import UndecodableResponseError, looks_undecoded
from app.scrapers.brickeconomy import BrickEconomyScraper


def test_real_html_is_not_flagged_as_undecoded():
    html = "<!doctype html><html><body><h1>LEGO 75414</h1><p>29,74 €</p></body></html>"
    assert looks_undecoded(html) is False


def test_empty_body_is_not_flagged():
    # Eine leere Antwort ist ein anderes Problem und hat ihre eigene Behandlung.
    assert looks_undecoded("") is False


def test_undecoded_compression_is_flagged():
    # So sah die BrickEconomy-Antwort in Produktion aus: Rohbytes, als Zeichen gelesen.
    garbage = bytes(range(1, 200)).decode("latin-1") * 20
    assert looks_undecoded(garbage) is True


def test_client_does_not_claim_encodings_it_cannot_decode():
    # httpx setzt Accept-Encoding selbst — passend zu den installierten Decodern.
    # Ein selbstgesetzter Header ist eine Behauptung, die niemand prueft.
    scraper = BrickEconomyScraper()
    assert "accept-encoding" not in {k.lower() for k in scraper._base_headers()}


@pytest.mark.asyncio
async def test_fetch_rejects_an_undecodable_body(monkeypatch):
    garbage = bytes(range(1, 200)).decode("latin-1") * 20

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, text=garbage, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with BrickEconomyScraper() as scraper:
        with pytest.raises(UndecodableResponseError):
            await scraper._fetch("https://www.brickeconomy.com/search?query=75414")


@pytest.mark.asyncio
async def test_fetch_passes_real_html_through(monkeypatch):
    html = "<!doctype html><html><body>LEGO 75414</body></html>"

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with BrickEconomyScraper() as scraper:
        body = await scraper._fetch("https://www.brickeconomy.com/search?query=75414")

    assert body == html


@pytest.mark.asyncio
async def test_brickeconomy_says_when_the_search_finds_nothing(monkeypatch, caplog):
    # Die einzige stumme Rueckgabe im ganzen Preis-Pfad: ohne Treffer stieg
    # get_price aus, ohne eine Zeile zu hinterlassen.
    async def no_hit(self, set_number):
        return None

    monkeypatch.setattr(BrickEconomyScraper, "_search_set", no_hit)

    async with BrickEconomyScraper() as scraper:
        with caplog.at_level("WARNING"):
            price = await scraper.get_price("75414")

    assert price is None
    assert "brickeconomy.set_not_found" in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Aus `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest tests/test_scraper_encoding.py -v
```

Erwartet: FAIL — `ImportError: cannot import name 'UndecodableResponseError' from 'app.scrapers.base'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/scrapers/base.py`, oberhalb der Klasse `BaseScraper` ergänzen:

```python
class UndecodableResponseError(RuntimeError):
    """Der Antwortkoerper ist keine Textseite.

    httpx gibt die Rohbytes zurueck, wenn es die Content-Encoding nicht
    entpacken kann; `.text` liest sie dann als Zeichen. Das Ergebnis ist
    ueberwiegend nicht druckbar, und jeder Parser dahinter sieht eine leere
    Seite — genau der Ausfall, der bei BrickEconomy fuenf Monate niemandem
    auffiel. Lieber laut scheitern als still nichts finden.
    """


# Echtes HTML enthaelt praktisch keine Steuerzeichen. Unentpackte Bytes
# bestehen zu rund einem Drittel daraus; der Abstand ist gross genug, dass
# die Grenze nicht fein justiert werden muss.
_UNDECODED_RATIO = 0.10
_UNDECODED_SAMPLE = 2000


def looks_undecoded(body: str) -> bool:
    """Ob ein Antwortkoerper Binaerdaten statt Text ist."""
    if not body:
        return False
    sample = body[:_UNDECODED_SAMPLE]
    odd = sum(
        1 for ch in sample
        if ch == "�" or (ord(ch) < 32 and ch not in "\t\r\n")
    )
    return odd / len(sample) > _UNDECODED_RATIO
```

In `BaseScraper` die Header-Erzeugung herausziehen und die `Accept-Encoding`-Zeile **entfernen**:

```python
    def _base_headers(self) -> dict[str, str]:
        """Header des gemeinsamen Clients.

        Ohne `Accept-Encoding`: httpx setzt den Header selbst, und zwar genau
        auf die Verfahren, fuer die ein Decoder installiert ist. Ein
        handgeschriebener Wert ist eine Behauptung ueber Faehigkeiten, die
        niemand gegen die Umgebung prueft.
        """
        return {
            "User-Agent": ua.random,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
        }
```

`_get_client` nutzt sie:

```python
            headers = self._base_headers()
```

Am Ende von `_fetch`, direkt vor `return response.text`:

```python
        body = response.text
        if looks_undecoded(body):
            logger.error(
                "scraper.undecodable_response",
                scraper=self.name,
                url=safe_url[:100],
                encoding=response.headers.get("content-encoding"),
            )
            raise UndecodableResponseError(
                f"{self.name}: Antwort von {safe_url[:80]} ist nicht dekodierbar "
                f"(content-encoding={response.headers.get('content-encoding')})"
            )
        return body
```

In `backend/app/scrapers/brickeconomy.py`, in `get_price`, den stummen Ausstieg mit einer Zeile versehen:

```python
            url = await self._search_set(set_number)
            if not url:
                logger.warning("brickeconomy.set_not_found", set_number=set_number)
                return None
```

In `backend/pyproject.toml` die httpx-Zeile ersetzen, damit `br` weiterhin angeboten werden **kann** — ein Client ohne Brotli sieht im Header-Fingerabdruck weniger nach Browser aus:

```toml
    "httpx[brotli]>=0.28.0",
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/Scripts/python.exe -m pip install -q ".[dev]"
./.venv/Scripts/python.exe -m pytest tests/test_scraper_encoding.py -v
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check .
```

Erwartet: neue Tests PASS, die bestehenden 295 weiterhin PASS, ruff sauber.

> Achtung: `test_brickmerge_price_uses_detail_page_and_ignores_savings` in `tests/test_price_scrapers.py` schiebt absichtlich Mojibake durch ein gefälschtes `_fetch`. Es ersetzt `_fetch` per monkeypatch komplett und wird vom neuen Wächter nicht berührt — läuft es trotzdem rot, ist der Wächter an der falschen Stelle eingehängt.

- [ ] **Step 5: Commit**

```bash
git add backend/app/scrapers/base.py backend/app/scrapers/brickeconomy.py backend/pyproject.toml backend/tests/test_scraper_encoding.py
git commit -m "fix(scrapers): stop claiming a compression the image cannot decode"
```

---

### Task 2: Der Dollarkurs kommt von der EZB

`USD_TO_EUR = 0.92` steht seit März 2026 fest im BrickEconomy-Scraper. Sobald die Quelle wieder liefert, verzerrt der Wert jeden zweiten Konsenspreis — und weil er nirgends datiert ist, fällt die Drift niemandem auf.

**Files:**
- Create: `backend/app/services/fx.py`
- Modify: `backend/app/security/url_policy.py` (EZB in die Allowlist)
- Modify: `backend/app/scrapers/brickeconomy.py` (Kurs statt Konstante)
- Test: `backend/tests/test_fx_rate.py` (neu)

**Interfaces:**
- Consumes: nichts aus Task 1
- Produces:
  - `app.services.fx.FxRate` — Dataclass mit `usd_to_eur: float`, `as_of: datetime | None`, `is_fallback: bool`
  - `app.services.fx.parse_ecb_rate(xml_text: str) -> float | None`
  - `async app.services.fx.get_usd_to_eur() -> FxRate`
  - `app.services.fx.FALLBACK_USD_TO_EUR: float`

- [ ] **Step 1: Write the failing test**

Neue Datei `backend/tests/test_fx_rate.py`:

```python
"""Der Dollarkurs ist ein Messwert, keine Konstante.

BrickEconomy quotiert in USD. Ein fest verdrahteter Kurs verzerrt jeden
Konsenspreis, und weil er kein Datum traegt, faellt die Drift niemandem auf.
Wenn kein Kurs zu holen ist, wird das vermerkt statt verschwiegen.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import fx

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube>
    <Cube time="2026-08-25">
      <Cube currency="USD" rate="1.0812"/>
      <Cube currency="JPY" rate="163.21"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


def test_parses_usd_rate_as_reciprocal():
    # Der Feed quotiert EUR->USD (Dollar je Euro). Gebraucht wird die Gegenrichtung.
    rate = fx.parse_ecb_rate(ECB_XML)
    assert rate == pytest.approx(1 / 1.0812, rel=1e-6)


def test_returns_none_when_usd_is_absent():
    without_usd = ECB_XML.replace('<Cube currency="USD" rate="1.0812"/>', "")
    assert fx.parse_ecb_rate(without_usd) is None


def test_returns_none_on_malformed_xml():
    assert fx.parse_ecb_rate("<not-xml") is None


def test_returns_none_on_implausible_rate():
    # Ein Kurs von 0 oder ein negativer Wert ist ein Parserfehler, kein Kurs.
    assert fx.parse_ecb_rate(ECB_XML.replace('rate="1.0812"', 'rate="0"')) is None


def test_cached_rate_is_fresh_within_a_day():
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    assert fx.is_fresh(now - timedelta(hours=23), now) is True
    assert fx.is_fresh(now - timedelta(hours=25), now) is False
    assert fx.is_fresh(None, now) is False


def test_fallback_rate_is_marked_as_such():
    rate = fx.FxRate(usd_to_eur=fx.FALLBACK_USD_TO_EUR, as_of=None, is_fallback=True)
    assert rate.is_fallback is True
    assert rate.note == "Ersatzkurs — kein EZB-Kurs verfuegbar"


def test_measured_rate_carries_its_date_and_no_note():
    as_of = datetime(2026, 8, 25, tzinfo=UTC)
    rate = fx.FxRate(usd_to_eur=0.925, as_of=as_of, is_fallback=False)
    assert rate.note is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_fx_rate.py -v
```

Erwartet: FAIL — `ModuleNotFoundError: No module named 'app.services.fx'`

- [ ] **Step 3: Write minimal implementation**

Neue Datei `backend/app/services/fx.py`:

```python
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
```

In `backend/app/security/url_policy.py`, `ALLOWED_HOSTS_BY_PLATFORM` ergänzen (alphabetisch zwischen `CATAWIKI` und `EBAY`):

```python
    "ECB": {"ecb.europa.eu"},
```

In `backend/app/scrapers/brickeconomy.py` die Konstante durch den Kurs ersetzen. `USD_TO_EUR` **löschen** und in `get_price` sowie `get_set_info` den Kurs holen:

```python
from app.services.fx import get_usd_to_eur
```

In `get_set_info`, an der UVP-Stelle:

```python
            retail_match = re.search(r"(?:Retail|RRP|MSRP)[:\s]*\$?([\d,.]+)", page_text)
            if retail_match:
                usd_price = float(retail_match.group(1).replace(",", ""))
                fx_rate = await get_usd_to_eur()
                info.uvp_eur = round(usd_price * fx_rate.usd_to_eur, 2)
```

In `get_price`, an der Preis-Stelle:

```python
            usd_price = float(value_match.group(1).replace(",", ""))
            fx_rate = await get_usd_to_eur()
            eur_price = round(usd_price * fx_rate.usd_to_eur, 2)
```

und den Vermerk in die Notiz aufnehmen, damit er im Protokoll landet:

```python
            growth_match = re.search(r"Growth[:\s]+([-+]?\d+[.,]?\d*)%", page_text)
            notes_parts = []
            if growth_match:
                notes_parts.append(f"Growth: {growth_match.group(1)}%")
            if fx_rate.note:
                notes_parts.append(fx_rate.note)
            notes = " | ".join(notes_parts) if notes_parts else None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_fx_rate.py -v
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check .
```

Erwartet: alle PASS, ruff sauber.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/fx.py backend/app/security/url_policy.py backend/app/scrapers/brickeconomy.py backend/tests/test_fx_rate.py
git commit -m "feat(fx): date the dollar rate against the ECB instead of a March constant"
```

---

### Task 3: Datenmodell und Migration für die Läufe

Zwei Tabellen und eine Spalte. Ohne sie hat der Task nichts, wohin er schreiben könnte.

**Files:**
- Create: `backend/app/models/valuation_run.py`
- Modify: `backend/app/models/__init__.py` (Export)
- Modify: `backend/app/models/inventory.py` (`reference_url`)
- Create: `backend/alembic/versions/d3a91c2f80b7_add_valuation_runs.py`
- Test: `backend/tests/test_migration_valuation_runs.py` (neu)

**Interfaces:**
- Consumes: nichts
- Produces:
  - `app.models.valuation_run.ValuationRun` (Tabelle `valuation_runs`)
  - `app.models.valuation_run.ValuationRunItem` (Tabelle `valuation_run_items`)
  - Enums `ValuationTrigger`, `ValuationRunStatus`, `ValuationOutcome`, `ValuationSkipReason`
  - `InventoryItem.reference_url: str | None`

- [ ] **Step 1: Write the failing test**

Neue Datei `backend/tests/test_migration_valuation_runs.py`:

```python
"""Die Protokoll-Migration muss die Tabellen bauen, die die Modelle erwarten.

CI faehrt `alembic upgrade head` gegen eine leere Datenbank — das beweist nur,
dass die Migration durchlaeuft, nicht dass sie zum Modell passt. Eine vergessene
Spalte faellt dort nicht auf und schlaegt erst beim ersten INSERT auf Prod zu.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.models.inventory import InventoryItem
from app.models.valuation_run import ValuationRun, ValuationRunItem

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "d3a91c2f80b7_add_valuation_runs.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_d3a91c2f80b7", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        # Die Migration haengt `reference_url` an eine bestehende Tabelle —
        # die muss es geben, sonst prueft der Test die falsche Sache.
        InventoryItem.__table__.create(conn)
        context = MigrationContext.configure(conn)
        module = _load_migration()
        module.op = Operations(context)
        module.upgrade()
        yield conn


def test_both_tables_exist(migrated):
    names = inspect(migrated).get_table_names()
    assert ValuationRun.__tablename__ in names
    assert ValuationRunItem.__tablename__ in names


@pytest.mark.parametrize("model", [ValuationRun, ValuationRunItem])
def test_every_model_column_exists_in_the_migration(migrated, model):
    actual = {c["name"] for c in inspect(migrated).get_columns(model.__tablename__)}
    expected = {c.name for c in model.__table__.columns}
    assert expected <= actual, f"fehlend: {sorted(expected - actual)}"


def test_inventory_gained_the_reference_url(migrated):
    actual = {c["name"] for c in inspect(migrated).get_columns(InventoryItem.__tablename__)}
    assert "reference_url" in actual


def test_run_items_are_removed_with_their_run(migrated):
    # Nachgemessen: der SQLite-Inspector liefert fuer diesen Fremdschluessel
    # `options == {}` — die Cascade-Aussage steht nur im DDL selbst. Ein Test
    # ueber get_foreign_keys() waere gruen, ohne irgendetwas zu pruefen.
    ddl = migrated.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE name = :name",
        {"name": ValuationRunItem.__tablename__},
    ).scalar_one()
    # Ohne Cascade bleiben beim Aufraeumen alter Laeufe verwaiste Zeilen stehen.
    assert "ON DELETE CASCADE" in ddl.upper()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_migration_valuation_runs.py -v
```

Erwartet: FAIL — `ModuleNotFoundError: No module named 'app.models.valuation_run'`

- [ ] **Step 3: Write minimal implementation**

Neue Datei `backend/app/models/valuation_run.py`:

```python
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
```

In `backend/app/models/inventory.py`, in der Purchase-info-Gruppe nach `buy_url`:

```python
    # Nachschlage-Link, den der Nutzer selbst setzt. BrickMerge und Idealo
    # werden aus der Setnummer erzeugt und brauchen keinen Speicher.
    reference_url: Mapped[str | None] = mapped_column(Text)
```

In `backend/app/models/__init__.py` importieren und exportieren:

```python
from app.models.valuation_run import (
    ValuationOutcome,
    ValuationRun,
    ValuationRunItem,
    ValuationRunStatus,
    ValuationSkipReason,
    ValuationTrigger,
)
```

und in `__all__` ergänzen:

```python
    "ValuationRun",
    "ValuationRunItem",
    "ValuationTrigger",
    "ValuationRunStatus",
    "ValuationOutcome",
    "ValuationSkipReason",
```

Neue Datei `backend/alembic/versions/d3a91c2f80b7_add_valuation_runs.py`:

```python
"""add valuation_runs, valuation_run_items and inventory_items.reference_url

Der Bewertungslauf meldete `errors: 0` bei 32 unbewerteten Sets, weil jede
uebersprungene Bewertung ein nacktes `continue` war. Diese Tabellen halten je
Lauf und je Set fest, was passiert ist und welche Quelle was geliefert hat.

Reines DDL, keine Datenlogik — die Tabellen sind beim Aufspielen leer, und
`reference_url` ist nullable.

Revision ID: d3a91c2f80b7
Revises: b8e5c30d7f14
Create Date: 2026-08-25 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3a91c2f80b7"
down_revision: str | None = "b8e5c30d7f14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuation_runs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("items_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("items_valued", sa.Integer(), server_default="0", nullable=False),
        sa.Column("items_skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("items_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "valuation_run_items",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("set_number", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("consensus_price", sa.Float(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        # Cascade, weil das Aufraeumen alter Laeufe sonst verwaiste Zeilen laesst.
        sa.ForeignKeyConstraint(["run_id"], ["valuation_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_valuation_run_items_run_id", "valuation_run_items", ["run_id"])
    op.add_column("inventory_items", sa.Column("reference_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("inventory_items", "reference_url")
    op.drop_index("ix_valuation_run_items_run_id", table_name="valuation_run_items")
    op.drop_table("valuation_run_items")
    op.drop_table("valuation_runs")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_migration_valuation_runs.py -v
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check .
```

Erwartet: alle PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/valuation_run.py backend/app/models/__init__.py backend/app/models/inventory.py backend/alembic/versions/d3a91c2f80b7_add_valuation_runs.py backend/tests/test_migration_valuation_runs.py
git commit -m "feat(inventory): give valuation runs somewhere to write down what they did"
```

---

### Task 4: Der Protokoll-Dienst

Sammelt je Set eine Zeile und legt sie am Ende gebündelt ab. Getrennt vom Task, damit die Buchführung ohne Scraper testbar ist.

**Files:**
- Create: `backend/app/services/valuation_log.py`
- Test: `backend/tests/test_valuation_log.py` (neu)

**Interfaces:**
- Consumes: `ValuationRun`, `ValuationRunItem`, `ValuationOutcome`, `ValuationSkipReason` aus Task 3
- Produces:
  - `app.services.valuation_log.SourceProbe` — Dataclass `source: str`, `price_eur: float | None = None`, `error: str | None = None`, `note: str | None = None`; Methode `as_dict() -> dict`
  - `app.services.valuation_log.ValuationRunRecorder` — `record_valued(...)`, `record_skipped(...)`, `record_failed(...)`, `counts() -> dict[str, int]`, `async flush(session, run_id) -> None`
  - `app.services.valuation_log.describe_sources(probes: list[SourceProbe]) -> str`
  - `async app.services.valuation_log.delete_runs_older_than(session, cutoff: datetime) -> int`

- [ ] **Step 1: Write the failing test**

Neue Datei `backend/tests/test_valuation_log.py`:

```python
"""Die Buchfuehrung des Bewertungslaufs.

Sie ist vom Task getrennt, damit sich pruefen laesst, dass jede Aussteige-
Stelle eine Zeile hinterlaesst — ohne einen einzigen Scraper zu starten.
"""

from app.models.valuation_run import ValuationOutcome, ValuationSkipReason
from app.services.valuation_log import (
    SourceProbe,
    ValuationRunRecorder,
    describe_sources,
)


def test_a_probe_keeps_price_and_reason_apart():
    ok = SourceProbe(source="BRICKMERGE", price_eur=29.74)
    blocked = SourceProbe(source="EBAY_SOLD", error="403 Forbidden")
    assert ok.as_dict() == {"source": "BRICKMERGE", "price_eur": 29.74, "error": None, "note": None}
    assert blocked.as_dict()["price_eur"] is None
    assert blocked.as_dict()["error"] == "403 Forbidden"


def test_sources_are_described_for_a_human():
    text = describe_sources([
        SourceProbe(source="BRICKECONOMY", error="kein Treffer"),
        SourceProbe(source="EBAY_SOLD", error="403 Forbidden"),
        SourceProbe(source="BRICKMERGE", price_eur=29.74),
    ])
    assert text == "BRICKECONOMY: kein Treffer · EBAY_SOLD: 403 Forbidden · BRICKMERGE: 29,74 €"


def test_a_note_is_appended_to_the_price():
    text = describe_sources([
        SourceProbe(source="BRICKECONOMY", price_eur=51.20, note="Ersatzkurs — kein EZB-Kurs verfuegbar"),
    ])
    assert text == "BRICKECONOMY: 51,20 € (Ersatzkurs — kein EZB-Kurs verfuegbar)"


def test_counts_match_the_recorded_rows():
    rec = ValuationRunRecorder()
    rec.record_valued(item_id=1, set_number="76430", consensus_price=56.98, probes=[])
    rec.record_skipped(
        item_id=2, set_number="40800",
        reason=ValuationSkipReason.SINGLE_SOURCE, probes=[],
    )
    rec.record_skipped(
        item_id=3, set_number="43230",
        reason=ValuationSkipReason.NO_PRICES, probes=[],
    )
    rec.record_failed(item_id=4, set_number="10280", detail="boom", probes=[])

    assert rec.counts() == {"total": 4, "valued": 1, "skipped": 2, "failed": 1}


def test_a_skip_always_carries_a_reason():
    rec = ValuationRunRecorder()
    rec.record_skipped(
        item_id=2, set_number="40800",
        reason=ValuationSkipReason.SINGLE_SOURCE,
        probes=[SourceProbe(source="BRICKMERGE", price_eur=14.34)],
    )
    row = rec.rows[0]
    assert row["outcome"] == ValuationOutcome.SKIPPED.value
    assert row["reason"] == "single_source"
    # Die Quellenlage ist die Diagnose — ohne sie ist die Zeile wertlos.
    assert row["sources"] == [
        {"source": "BRICKMERGE", "price_eur": 14.34, "error": None, "note": None}
    ]
    assert row["detail"] == "BRICKMERGE: 14,34 €"


def test_a_failure_records_its_message():
    rec = ValuationRunRecorder()
    rec.record_failed(item_id=9, set_number="75251", detail="TimeoutError", probes=[])
    row = rec.rows[0]
    assert row["outcome"] == ValuationOutcome.FAILED.value
    assert row["reason"] == ValuationSkipReason.EXCEPTION.value
    assert row["detail"] == "TimeoutError"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_valuation_log.py -v
```

Erwartet: FAIL — `ModuleNotFoundError: No module named 'app.services.valuation_log'`

- [ ] **Step 3: Write minimal implementation**

Neue Datei `backend/app/services/valuation_log.py`:

```python
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
```

`format_eur` gibt es noch nicht — in `backend/app/domain/german.py` ergänzen:

```python
def format_eur(value: float) -> str:
    """Deutscher Eurobetrag: 29,74 € und 1.234,56 €.

    Erst die Zahl umstellen, dann das Zeichen anhaengen. Ein
    Zwischentausch ueber ein Trennzeichen wuerde auch gehen, braucht
    dafuer aber ein Zeichen, das sonst nicht vorkommt — und ein
    unsichtbares im Quelltext ist eine Falle beim Abtippen.
    """
    formatted = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{formatted} €"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_valuation_log.py -v
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check .
```

Erwartet: alle PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/valuation_log.py backend/app/domain/german.py backend/tests/test_valuation_log.py
git commit -m "feat(inventory): record what each source contributed, not just the number"
```

---

### Task 5: Der Bewertungslauf schreibt mit

Die vier stummen `continue` in `_update_valuations_async` bekommen je einen Grund, der Rückgabewert wird ehrlich, und das Zeitlimit passt zur tatsächlichen Laufzeit (658 s gemessen, `task_time_limit` steht auf 600).

**Files:**
- Modify: `backend/app/tasks/update_inventory.py`
- Test: `backend/tests/test_inventory_valuation_run.py` (neu)

**Interfaces:**
- Consumes: `ValuationRunRecorder`, `SourceProbe`, `delete_runs_older_than` (Task 4); `ValuationRun`, `ValuationTrigger`, `ValuationRunStatus`, `ValuationSkipReason` (Task 3)
- Produces:
  - `app.tasks.update_inventory.update_inventory_valuations(run_id: int | None = None) -> dict` — Celery-Task, Rückgabe `{"run_id": int, "total": int, "valued": int, "skipped": int, "failed": int}`
  - `async app.tasks.update_inventory._collect_prices(item, uvp) -> tuple[list[ScrapedPrice], list[SourceProbe]]`
  - `app.tasks.update_inventory.RUN_RETENTION_DAYS: int = 30`

- [ ] **Step 1: Write the failing test**

Neue Datei `backend/tests/test_inventory_valuation_run.py`:

```python
"""Jede Aussteige-Stelle des Bewertungslaufs muss eine Spur hinterlassen.

Der Lauf vom 25.08.2026 meldete `{'updated': 3, 'errors': 0}` bei 41
gehaltenen Sets. 38 Sets fielen durch `is_persistable_consensus` — jedes ueber
ein nacktes `continue`, das weder einen Zaehler erhoehte noch irgendwo ankam.
"""

import pytest

from app.models.valuation_run import ValuationSkipReason
from app.scrapers.base import ScrapedPrice
from app.services.valuation_log import SourceProbe, ValuationRunRecorder
from app.tasks.update_inventory import _classify_consensus
from app.engine.market_consensus import calculate_consensus


def _price(source: str, value: float) -> ScrapedPrice:
    return ScrapedPrice(source=source, price_eur=value)


def test_a_single_source_is_a_named_skip():
    consensus = calculate_consensus([_price("BRICKMERGE", 14.34)])
    outcome, reason = _classify_consensus(consensus)
    assert outcome == "skipped"
    assert reason is ValuationSkipReason.SINGLE_SOURCE


def test_no_prices_at_all_is_its_own_reason():
    consensus = calculate_consensus([])
    outcome, reason = _classify_consensus(consensus)
    assert outcome == "skipped"
    assert reason is ValuationSkipReason.NO_PRICES


def test_two_sources_far_apart_are_a_divergence_skip():
    # 30 % Streuung ist die Grenze in is_persistable_consensus.
    consensus = calculate_consensus([_price("BRICKMERGE", 20.0), _price("BRICKECONOMY", 60.0)])
    outcome, reason = _classify_consensus(consensus)
    assert outcome == "skipped"
    assert reason is ValuationSkipReason.DIVERGENCE


def test_two_agreeing_sources_are_valued():
    consensus = calculate_consensus([_price("BRICKMERGE", 55.0), _price("BRICKECONOMY", 58.0)])
    outcome, reason = _classify_consensus(consensus)
    assert outcome == "valued"
    assert reason is None


def test_the_recorder_sees_every_item_even_when_nothing_is_valued():
    rec = ValuationRunRecorder()
    for set_number in ("40793", "40800", "40795"):
        rec.record_skipped(
            item_id=None, set_number=set_number,
            reason=ValuationSkipReason.SINGLE_SOURCE,
            probes=[SourceProbe(source="BRICKMERGE", price_eur=9.99)],
        )
    counts = rec.counts()
    assert counts["total"] == 3
    assert counts["valued"] == 0
    assert counts["skipped"] == 3
    # Der alte Rueckgabewert haette hier errors: 0 gemeldet und sonst nichts.
    assert all(row["reason"] == "single_source" for row in rec.rows)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_inventory_valuation_run.py -v
```

Erwartet: FAIL — `ImportError: cannot import name '_classify_consensus' from 'app.tasks.update_inventory'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/tasks/update_inventory.py`. Neue Importe oben:

```python
from datetime import UTC, datetime, timedelta

from app.models.valuation_run import (
    ValuationRun,
    ValuationRunStatus,
    ValuationSkipReason,
    ValuationTrigger,
)
from app.services.valuation_log import (
    SourceProbe,
    ValuationRunRecorder,
    delete_runs_older_than,
)

RUN_RETENTION_DAYS = 30
```

Die Einstufung als eigene, reine Funktion — sie ist die Stelle, an der bisher stumm ausgestiegen wurde:

```python
def _classify_consensus(consensus) -> tuple[str, ValuationSkipReason | None]:
    """Ob ein Konsens gespeichert wird — und wenn nicht, warum nicht.

    Rein und ohne I/O, damit jede der vier Aussteige-Stellen einzeln
    pruefbar ist. `is_persistable_consensus` liefert nur ja/nein; fuer das
    Protokoll wird der Grund gebraucht.
    """
    if consensus.num_sources == 0:
        return "skipped", ValuationSkipReason.NO_PRICES
    if consensus.consensus_price <= 0:
        return "skipped", ValuationSkipReason.ZERO_CONSENSUS
    if consensus.num_sources < 2:
        return "skipped", ValuationSkipReason.SINGLE_SOURCE
    if consensus.divergence_percent > 0.30:
        return "skipped", ValuationSkipReason.DIVERGENCE
    return "valued", None
```

Die Preisbeschaffung wird herausgezogen und meldet je Quelle, was sie beigetragen hat:

```python
async def _collect_prices(item, uvp: float | None) -> tuple[list, list[SourceProbe]]:
    """Preise aller Quellen — und je Quelle die Spur, warum sie nichts lieferte."""
    prices = []
    probes: list[SourceProbe] = []
    for scraper_cls in PRICE_SCRAPERS:
        name = scraper_cls.__name__
        try:
            async with scraper_cls() as scraper:
                price = await scraper.get_price(item.set_number)
        except Exception as exc:  # noqa: BLE001 — eine tote Quelle darf den Lauf nicht beenden
            probes.append(SourceProbe(source=name, error=f"{type(exc).__name__}: {exc}"[:200]))
            continue

        if price is None:
            probes.append(SourceProbe(source=name, error="kein Preis gefunden"))
            continue

        if not is_plausible_price(price.price_eur, uvp):
            logger.warning(
                "inventory.implausible_price",
                set_number=item.set_number, source=price.source, price=price.price_eur,
            )
            probes.append(SourceProbe(
                source=price.source, price_eur=price.price_eur,
                error=f"unplausibel gegen UVP {uvp}",
            ))
            continue

        probes.append(SourceProbe(source=price.source, price_eur=price.price_eur, note=price.notes))
        prices.append(price)
    return prices, probes
```

Der Task nimmt eine `run_id` entgegen und hebt sein Zeitlimit:

```python
@celery_app.task(
    name="app.tasks.update_inventory.update_inventory_valuations",
    # Gemessen: 658 s fuer 41 Sets. Das globale Limit von 600 s war bereits
    # gerissen und trug nur, weil der aktuelle Worker-Pool es nicht erzwingt.
    time_limit=3600,
    soft_time_limit=3300,
)
def update_inventory_valuations(run_id: int | None = None) -> dict:
    return _run_async(_update_valuations_async(run_id))
```

`_update_valuations_async` wird umgebaut. Der Kopf legt bei Bedarf den Lauf an:

```python
async def _update_valuations_async(run_id: int | None = None) -> dict:
    now = datetime.utcnow()  # naive datetime to match current DB column setup
    recorder = ValuationRunRecorder()

    async with async_session() as session:
        if run_id is None:
            run = ValuationRun(
                started_at=datetime.now(UTC),
                trigger=ValuationTrigger.SCHEDULED.value,
                status=ValuationRunStatus.RUNNING.value,
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
            run_id = run.id
```

Danach unverändert die Set-Metadaten und die Item-Liste laden. Die Schleife ersetzt die stummen `continue`:

```python
        for item in items:
            try:
                prices, probes = await _collect_prices(item, uvp_by_set.get(item.set_number))
                consensus = calculate_consensus(prices)
                outcome, reason = _classify_consensus(consensus)

                if outcome == "skipped":
                    logger.info(
                        "inventory.valuation_skipped",
                        set_number=item.set_number, reason=reason.value,
                        sources=consensus.num_sources,
                    )
                    recorder.record_skipped(
                        item_id=item.id, set_number=item.set_number,
                        reason=reason, probes=probes,
                        consensus_price=consensus.consensus_price or None,
                    )
                    continue

                total_invested = item.buy_price + (item.buy_shipping or 0)
                item.current_market_price = round(consensus.consensus_price, 2)
                item.market_price_updated_at = now
                item.unrealized_profit = round(consensus.consensus_price - total_invested, 2)
                item.unrealized_roi_percent = (
                    round(((consensus.consensus_price - total_invested) / total_invested) * 100, 1)
                    if total_invested > 0 else 0
                )
```

Der Signal-Block darunter bleibt wie er ist. Statt `summary["updated"] += 1` am Ende des `try`:

```python
                recorder.record_valued(
                    item_id=item.id, set_number=item.set_number,
                    consensus_price=item.current_market_price, probes=probes,
                )
            except Exception as exc:
                logger.error("inventory.update_failed", set_number=item.set_number, error=str(exc))
                recorder.record_failed(
                    item_id=item.id, set_number=item.set_number,
                    detail=f"{type(exc).__name__}: {exc}"[:500], probes=[],
                )
```

Der Abschluss ersetzt den alten `summary`-Block:

```python
        await recorder.flush(session, run_id)
        run = await session.get(ValuationRun, run_id)
        if run is not None:
            run.finished_at = datetime.now(UTC)
            run.status = ValuationRunStatus.SUCCESS.value
        await delete_runs_older_than(
            session, datetime.now(UTC) - timedelta(days=RUN_RETENTION_DAYS)
        )
        await session.commit()

    counts = recorder.counts()
    logger.info("inventory.valuations_updated", run_id=run_id, **counts)
    return {"run_id": run_id, **counts}
```

Der `sell_signals`-Zähler entfällt aus dem Rückgabewert — er stand für „wie viele Signale sind aktiv", nicht für Nutzarbeit, und die Zahl steht ohnehin in der Portfolio-Zusammenfassung.

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_inventory_valuation_run.py -v
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check .
```

Erwartet: alle PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/tasks/update_inventory.py backend/tests/test_inventory_valuation_run.py
git commit -m "fix(inventory): give every skipped valuation a reason and a row"
```

---

### Task 6: Der Watchdog erkennt einen Lauf, der nichts bewertet

`check_pipeline_health` misst „Task lief". Ein Lauf, der 32 von 41 Sets überspringt, meldet weiterhin `success`. Dieselbe Lücke, die `evaluate_data_freshness` für die Preisdaten schon schließt.

**Files:**
- Modify: `backend/app/services/heartbeat.py`
- Modify: `backend/app/tasks/health_check.py`
- Test: `backend/tests/test_valuation_coverage.py` (neu)

**Interfaces:**
- Consumes: `ValuationRun` (Task 3)
- Produces: `app.services.heartbeat.evaluate_valuation_coverage(run: ValuationRun | None, now: datetime) -> TaskHealth | None`, `app.services.heartbeat.MIN_ITEMS_FOR_COVERAGE_CHECK: int`, `app.services.heartbeat.MAX_SKIPPED_SHARE: float`

- [ ] **Step 1: Write the failing test**

Neue Datei `backend/tests/test_valuation_coverage.py`:

```python
"""Gruen soll wieder heissen, dass Werte entstehen.

Der Lauf vom 25.08.2026 uebersprang 32 von 41 Sets und meldete `success`.
Der Watchdog misst Task-Erfolg, nicht Nutzarbeit — dieselbe Luecke, die
evaluate_data_freshness fuer die Preisdaten bereits schliesst.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.heartbeat import evaluate_valuation_coverage

NOW = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def _run(total, valued, skipped, failed=0):
    return SimpleNamespace(
        id=1, started_at=NOW, items_total=total, items_valued=valued,
        items_skipped=skipped, items_failed=failed,
    )


def test_no_run_yet_is_not_a_problem():
    assert evaluate_valuation_coverage(None, NOW) is None


def test_a_healthy_run_reports_nothing():
    assert evaluate_valuation_coverage(_run(41, 38, 3), NOW) is None


def test_the_production_run_is_flagged():
    health = evaluate_valuation_coverage(_run(41, 9, 32), NOW)
    assert health is not None
    assert health.status == "stale"
    assert "32 von 41" in health.detail


def test_an_empty_inventory_is_not_a_problem():
    assert evaluate_valuation_coverage(_run(0, 0, 0), NOW) is None


def test_a_tiny_inventory_does_not_trip_the_share():
    # Bei zwei Sets ist ein Uebersprung schon ueber der Haelfte, sagt aber nichts.
    assert evaluate_valuation_coverage(_run(2, 1, 1), NOW) is None


def test_exactly_half_skipped_is_still_fine():
    assert evaluate_valuation_coverage(_run(10, 5, 5), NOW) is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_valuation_coverage.py -v
```

Erwartet: FAIL — `ImportError: cannot import name 'evaluate_valuation_coverage'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/services/heartbeat.py`, nach `evaluate_data_freshness`:

```python
# Unterhalb dieser Anzahl sagt der Anteil nichts: bei zwei Sets ist ein
# Uebersprung schon die Haelfte.
MIN_ITEMS_FOR_COVERAGE_CHECK = 5
MAX_SKIPPED_SHARE = 0.5


def evaluate_valuation_coverage(run, now: datetime) -> TaskHealth | None:
    """Nutzarbeit der Bewertung: ein Lauf muss Werte erzeugen, nicht nur laufen.

    Der Heartbeat sieht nur, dass der Task durchlief. Ein Lauf, der jedes
    zweite Set ueberspringt, ist ein Quellenausfall — und genau der blieb
    fuenf Monate unbemerkt. Rein (kein I/O); gibt ein synthetisches Problem
    oder None zurueck.
    """
    if run is None or run.items_total < MIN_ITEMS_FOR_COVERAGE_CHECK:
        return None

    share = run.items_skipped / run.items_total
    if share <= MAX_SKIPPED_SHARE:
        return None

    return TaskHealth(
        task_name="pipeline.valuation_coverage",
        status="stale",
        last_success_at=run.started_at,
        last_run_at=run.started_at,
        last_status=None,
        age_seconds=(now - run.started_at).total_seconds() if run.started_at else None,
        max_age_seconds=0,
        detail=(
            f"Letzter Bewertungslauf: {run.items_skipped} von {run.items_total} Sets "
            f"ohne Marktwert — Quellenlage im Protokoll pruefen"
        ),
    )
```

In `backend/app/tasks/health_check.py`: Import ergänzen, den jüngsten abgeschlossenen Lauf in Phase 1 mitladen und den Check direkt neben `evaluate_data_freshness` einhängen.

Bei den Importen von `app.services.heartbeat` ergänzen:

```python
from app.models.valuation_run import ValuationRun
from app.services.heartbeat import (
    evaluate_data_freshness,
    evaluate_health,
    evaluate_valuation_coverage,
    filter_unthrottled,
    load_heartbeats,
)
```

In `_check_async`, im bestehenden `async with async_session() as session:`-Block
(Phase 1, nur Lesen) nach der `active_watchlist`-Abfrage:

```python
        latest_run = (
            await session.execute(
                select(ValuationRun)
                .where(ValuationRun.finished_at.is_not(None))
                .order_by(ValuationRun.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
```

Und direkt unter dem `freshness_problem`-Block:

```python
    coverage_problem = evaluate_valuation_coverage(latest_run, now)
    if coverage_problem is not None:
        problems.append(coverage_problem)
```

`select` ist in der Datei bereits importiert; `func` ebenfalls. Die
Alarm-Drosselung greift automatisch: `filter_unthrottled` legt für
`pipeline.valuation_coverage` beim ersten Alarm eine Heartbeat-Zeile an,
genau wie für `pipeline.price_data_freshness`.

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_valuation_coverage.py -v
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check .
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/heartbeat.py backend/app/tasks/health_check.py backend/tests/test_valuation_coverage.py
git commit -m "fix(health): a run that values nothing is not a healthy run"
```

---

### Task 7: Endpunkte für Lauf, Protokoll und Dubletten

**Files:**
- Modify: `backend/app/api/routes/inventory.py`
- Test: `backend/tests/test_inventory_valuation_api.py` (neu)

**Interfaces:**
- Consumes: `ValuationRun`, `ValuationRunItem`, `ValuationRunStatus`, `ValuationTrigger` (Task 3); `update_inventory_valuations` (Task 5)
- Produces:
  - `POST /api/inventory/valuation/run` → `{"run_id": int}`; `409` mit `{"detail": {"message": str, "run_id": int}}` bei laufendem Lauf
  - `GET /api/inventory/valuation/runs?limit=20` → `list[ValuationRunResponse]`
  - `GET /api/inventory/valuation/runs/{run_id}` → `ValuationRunDetailResponse`
  - `GET /api/inventory/lookup?set_number=40800` → `list[InventoryLookupResponse]`
  - `app.api.routes.inventory.STALE_RUN_MINUTES: int = 30`
  - `app.api.routes.inventory.is_run_blocking(run, now) -> bool`

- [ ] **Step 1: Write the failing test**

Neue Datei `backend/tests/test_inventory_valuation_api.py`:

```python
"""Der Knopf darf keinen zweiten Lauf starten — und ein toter Lauf darf ihn
nicht fuer immer sperren.

Ein Lauf dauert gemessen elf Minuten. Zwei parallele Laeufe wuerden dieselben
Quellen doppelt befragen und den Block beschleunigen. Ein abgestuerzter Lauf
bleibt dagegen auf `running` stehen und wuerde die Bewertung dauerhaft
blockieren, wenn ihn nichts abloest.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.api.routes.inventory import STALE_RUN_MINUTES, is_run_blocking

NOW = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def _running(minutes_ago: int):
    return SimpleNamespace(
        id=7, status="running", started_at=NOW - timedelta(minutes=minutes_ago)
    )


def test_no_run_does_not_block():
    assert is_run_blocking(None, NOW) is False


def test_a_fresh_running_run_blocks():
    assert is_run_blocking(_running(5), NOW) is True


def test_a_run_at_the_limit_still_blocks():
    assert is_run_blocking(_running(STALE_RUN_MINUTES - 1), NOW) is True


def test_a_stale_running_run_no_longer_blocks():
    # Sonst sperrt ein abgestuerzter Worker den Knopf fuer immer.
    assert is_run_blocking(_running(STALE_RUN_MINUTES + 1), NOW) is False


def test_a_finished_run_does_not_block():
    finished = SimpleNamespace(id=7, status="success", started_at=NOW - timedelta(minutes=2))
    assert is_run_blocking(finished, NOW) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_inventory_valuation_api.py -v
```

Erwartet: FAIL — `ImportError: cannot import name 'STALE_RUN_MINUTES'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/api/routes/inventory.py`. Neue Importe:

```python
# Die bestehende Zeile `from datetime import date, datetime` erweitern:
from datetime import UTC, date, datetime, timedelta

from app.models.valuation_run import (
    ValuationRun,
    ValuationRunItem,
    ValuationRunStatus,
    ValuationTrigger,
)
from app.tasks.celery_app import celery_app

STALE_RUN_MINUTES = 30
```

Antwortmodelle bei den anderen `BaseModel`-Klassen:

```python
class ValuationRunResponse(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    trigger: str
    status: str
    items_total: int
    items_valued: int
    items_skipped: int
    items_failed: int
    error: str | None

    model_config = {"from_attributes": True}


class ValuationRunItemResponse(BaseModel):
    set_number: str
    item_id: int | None
    outcome: str
    reason: str | None
    detail: str | None
    sources: list | None
    consensus_price: float | None

    model_config = {"from_attributes": True}


class ValuationRunDetailResponse(ValuationRunResponse):
    items: list[ValuationRunItemResponse] = []


class InventoryLookupResponse(BaseModel):
    id: int
    set_number: str
    set_name: str
    quantity: int
    buy_price: float
    buy_date: date
    buy_platform: str | None
```

Die Sperr-Regel als reine Funktion:

```python
def is_run_blocking(run, now: datetime) -> bool:
    """Ob ein vorhandener Lauf einen neuen Start verhindert.

    Ein Lauf dauert gemessen elf Minuten; zwei parallele wuerden dieselben
    Quellen doppelt befragen. Ein abgestuerzter Worker laesst seinen Lauf
    dagegen auf `running` stehen — ohne Verfallsdatum bliebe der Knopf fuer
    immer gesperrt.
    """
    if run is None or run.status != ValuationRunStatus.RUNNING.value:
        return False
    started = run.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if started is None:
        return False
    return (now - started) < timedelta(minutes=STALE_RUN_MINUTES)
```

Die Endpunkte. **Wichtig:** Sie müssen **vor** `@router.get("/{item_id}/...")`-Routen stehen, sonst schluckt der Pfadparameter `valuation` und `lookup`.

```python
@router.get("/lookup", response_model=list[InventoryLookupResponse])
async def lookup_by_set_number(
    set_number: str = Query(min_length=1, max_length=20),
    session: AsyncSession = Depends(get_session),
):
    """Gehaltene Eintraege zu einer Setnummer — Grundlage des Dubletten-Hinweises.

    Nur HOLDING: Verkauftes ist keine Dublette.
    """
    result = await session.execute(
        select(InventoryItem)
        .where(
            InventoryItem.set_number == set_number.strip(),
            InventoryItem.status == InventoryStatus.HOLDING.value,
        )
        .order_by(InventoryItem.buy_date.desc())
    )
    return [
        InventoryLookupResponse(
            id=item.id, set_number=item.set_number, set_name=item.set_name,
            quantity=item.quantity or 1, buy_price=item.buy_price,
            buy_date=item.buy_date, buy_platform=item.buy_platform,
        )
        for item in result.scalars().all()
    ]


@router.post("/valuation/run")
async def start_valuation_run(session: AsyncSession = Depends(get_session)):
    """Bewertung von Hand anstossen."""
    now = datetime.now(UTC)
    latest = (
        await session.execute(
            select(ValuationRun).order_by(ValuationRun.started_at.desc()).limit(1)
        )
    ).scalar_one_or_none()

    if is_run_blocking(latest, now):
        raise HTTPException(
            status_code=409,
            detail={"message": "Es läuft bereits eine Aktualisierung.", "run_id": latest.id},
        )

    if latest is not None and latest.status == ValuationRunStatus.RUNNING.value:
        # Nicht mehr blockierend, aber noch auf "running": der Worker ist
        # gestorben. Ohne diesen Abschluss steht der Lauf fuer immer als
        # laufend in der Statusleiste und der Knopf bleibt gesperrt.
        latest.status = ValuationRunStatus.FAILED.value
        latest.finished_at = now
        latest.error = (
            f"Kein Abschluss nach {STALE_RUN_MINUTES} Minuten \u2014 als abgebrochen gewertet"
        )

    run = ValuationRun(
        started_at=now,
        trigger=ValuationTrigger.MANUAL.value,
        status=ValuationRunStatus.RUNNING.value,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    # Der Lauf existiert, bevor er angestossen wird: sonst gaebe es den
    # Zustand "gestartet, aber nirgends sichtbar".
    celery_app.send_task(
        "app.tasks.update_inventory.update_inventory_valuations",
        kwargs={"run_id": run.id},
        queue="analysis",
    )
    logger.info("inventory.valuation_run_started", run_id=run.id)
    return {"run_id": run.id}


@router.get("/valuation/runs", response_model=list[ValuationRunResponse])
async def list_valuation_runs(
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(ValuationRun).order_by(ValuationRun.started_at.desc()).limit(limit)
    )
    return [ValuationRunResponse.model_validate(run) for run in result.scalars().all()]


@router.get("/valuation/runs/{run_id}", response_model=ValuationRunDetailResponse)
async def get_valuation_run(run_id: int, session: AsyncSession = Depends(get_session)):
    run = await session.get(ValuationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Lauf {run_id} nicht gefunden")
    items = (
        await session.execute(
            select(ValuationRunItem)
            .where(ValuationRunItem.run_id == run_id)
            .order_by(ValuationRunItem.outcome.asc(), ValuationRunItem.set_number.asc())
        )
    ).scalars().all()
    return ValuationRunDetailResponse(
        **ValuationRunResponse.model_validate(run).model_dump(),
        items=[ValuationRunItemResponse.model_validate(i) for i in items],
    )
```

`InventoryAdd`, `InventoryUpdate`, `InventoryResponse` und `_to_response` um `reference_url` ergänzen:

```python
    reference_url: str | None = None
```

in `InventoryAdd`/`InventoryUpdate`, `reference_url: str | None` in `InventoryResponse`, `reference_url=item.reference_url` in `_to_response`, und in `add_inventory_item` das Feld an den Konstruktor.

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_inventory_valuation_api.py -v
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check .
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/inventory.py backend/tests/test_inventory_valuation_api.py
git commit -m "feat(inventory): expose the run trigger, the log and a duplicate lookup"
```

---

### Task 8: Frontend — Statusleiste und Aktualisieren-Knopf

Ersetzt den Satz „Marktwerte werden automatisch alle 6 Stunden aktualisiert" durch etwas, das den tatsächlichen Zustand zeigt.

**Files:**
- Modify: `frontend/src/api/client.js`
- Create: `frontend/src/components/ValuationStatus.jsx`
- Modify: `frontend/src/pages/Inventar.jsx` (Zeile mit dem 6-Stunden-Satz, aktuell `:497`)

**Interfaces:**
- Consumes: die Endpunkte aus Task 7
- Produces: `api.startValuationRun()`, `api.listValuationRuns(limit)`, `api.getValuationRun(id)`, `api.lookupInventory(setNumber)`; Komponente `<ValuationStatus />`

- [ ] **Step 1: Add the API client functions**

In `frontend/src/api/client.js`, bei den anderen Inventar-Funktionen:

```js
  startValuationRun: () => request("/inventory/valuation/run", { method: "POST" }),
  listValuationRuns: (limit = 20) => request(`/inventory/valuation/runs?limit=${limit}`),
  getValuationRun: (id) => request(`/inventory/valuation/runs/${id}`),
  lookupInventory: (setNumber) =>
    request(`/inventory/lookup?set_number=${encodeURIComponent(setNumber)}`),
```

- [ ] **Step 2: Write the status component**

Neue Datei `frontend/src/components/ValuationStatus.jsx`:

```jsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";

function formatWhen(iso) {
  if (!iso) return "nie";
  const d = new Date(iso);
  return d.toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function summarize(run) {
  if (!run) return "Noch kein Lauf aufgezeichnet.";
  if (run.status === "running") return "Aktualisierung läuft …";
  const parts = [`${run.items_valued} von ${run.items_total} bewertet`];
  if (run.items_skipped) parts.push(`${run.items_skipped} übersprungen`);
  if (run.items_failed) parts.push(`${run.items_failed} fehlgeschlagen`);
  return parts.join(" · ");
}

export default function ValuationStatus() {
  const queryClient = useQueryClient();
  const { data: runs = [] } = useQuery({
    queryKey: ["valuationRuns"],
    queryFn: () => api.listValuationRuns(1),
    // Ein Lauf dauert rund elf Minuten. Solange er laeuft, muss die Anzeige
    // nachziehen — sonst klickt man ihn ein zweites Mal an.
    refetchInterval: (query) =>
      query.state.data?.[0]?.status === "running" ? 10_000 : 60_000,
  });

  const latest = runs[0];
  const running = latest?.status === "running";

  const start = useMutation({
    mutationFn: api.startValuationRun,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["valuationRuns"] }),
  });

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 mb-4 text-xs">
      <span className="text-text-muted">
        Letzte Aktualisierung: {formatWhen(latest?.finished_at || latest?.started_at)} — {summarize(latest)}
      </span>
      <button
        type="button"
        onClick={() => start.mutate()}
        disabled={running || start.isPending}
        className="px-3 py-1 rounded-lg bg-lego-yellow text-bg-primary font-medium disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {running ? "Läuft …" : "Jetzt aktualisieren"}
      </button>
      <Link to="/protokoll" className="text-lego-yellow hover:underline">
        Protokoll
      </Link>
      {start.isError && (
        <span className="text-status-danger">
          {start.error?.message || "Start fehlgeschlagen"}
        </span>
      )}
      <span className="text-text-muted">Automatisch alle 6 Stunden.</span>
    </div>
  );
}
```

- [ ] **Step 3: Wire it into the inventory page**

In `frontend/src/pages/Inventar.jsx` den Import ergänzen und die Zeile

```jsx
{summary && <p className="text-text-muted text-xs mb-4">Marktwerte werden automatisch alle 6 Stunden aktualisiert.</p>}
```

ersetzen durch:

```jsx
<ValuationStatus />
```

- [ ] **Step 4: Verify**

```bash
npm run lint
npm run build
```

Erwartet: beides ohne Fehler. Die Route `/protokoll` folgt in Task 9 — der Link zeigt bis dahin auf die Weiterleitung nach `/`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.js frontend/src/components/ValuationStatus.jsx frontend/src/pages/Inventar.jsx
git commit -m "feat(inventory): say when the valuation last ran, and let it be started"
```

---

### Task 9: Frontend — Protokoll-Seite

**Files:**
- Create: `frontend/src/pages/ValuationLog.jsx`
- Modify: `frontend/src/App.jsx` (Route)

**Interfaces:**
- Consumes: `api.listValuationRuns`, `api.getValuationRun` (Task 8)
- Produces: Route `/protokoll`

- [ ] **Step 1: Write the page**

Neue Datei `frontend/src/pages/ValuationLog.jsx`:

```jsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";

const OUTCOME_LABEL = {
  valued: "bewertet",
  skipped: "übersprungen",
  failed: "fehlgeschlagen",
};

const REASON_LABEL = {
  no_prices: "keine Quelle lieferte einen Preis",
  zero_consensus: "Konsens ergab keinen Betrag",
  single_source: "nur eine Quelle — zu wenig für einen Konsens",
  divergence: "Quellen weichen zu stark voneinander ab",
  implausible_price: "Preis unplausibel gegen UVP",
  exception: "Fehler während der Bewertung",
};

function formatWhen(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function RunDetail({ runId }) {
  const { data, isLoading } = useQuery({
    queryKey: ["valuationRun", runId],
    queryFn: () => api.getValuationRun(runId),
  });

  if (isLoading) return <p className="text-text-muted text-xs p-3">Wird geladen …</p>;
  if (!data?.items?.length) return <p className="text-text-muted text-xs p-3">Keine Zeilen.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-muted text-left">
            <th className="p-2">Set</th>
            <th className="p-2">Ergebnis</th>
            <th className="p-2">Grund</th>
            <th className="p-2">Quellenlage</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((item, index) => (
            <tr key={`${item.set_number}-${index}`} className="border-t border-bg-hover">
              <td className="p-2 font-[family-name:var(--font-mono)] text-lego-yellow">
                {item.set_number}
              </td>
              <td className="p-2">{OUTCOME_LABEL[item.outcome] || item.outcome}</td>
              <td className="p-2 text-text-secondary">
                {item.reason ? REASON_LABEL[item.reason] || item.reason : "—"}
              </td>
              <td className="p-2 text-text-muted">{item.detail || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ValuationLog() {
  const [openRunId, setOpenRunId] = useState(null);
  const { data: runs = [], isLoading } = useQuery({
    queryKey: ["valuationRuns", "all"],
    queryFn: () => api.listValuationRuns(30),
  });

  return (
    <div className="p-4">
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="text-xl font-bold">Bewertungs-Protokoll</h1>
        <Link to="/inventar" className="text-lego-yellow text-sm hover:underline">
          zurück zum Inventar
        </Link>
      </div>
      <p className="text-text-muted text-xs mb-4">
        Läufe der letzten 30 Tage. Ältere werden automatisch entfernt.
      </p>

      {isLoading && <p className="text-text-muted text-sm">Wird geladen …</p>}
      {!isLoading && runs.length === 0 && (
        <p className="text-text-muted text-sm">Noch kein Lauf aufgezeichnet.</p>
      )}

      <div className="space-y-2">
        {runs.map((run) => (
          <div key={run.id} className="border border-bg-hover rounded-lg">
            <button
              type="button"
              onClick={() => setOpenRunId(openRunId === run.id ? null : run.id)}
              className="w-full flex flex-wrap items-center justify-between gap-2 p-3 text-left text-sm hover:bg-bg-hover/40"
            >
              <span>
                {formatWhen(run.started_at)}
                <span className="text-text-muted ml-2">
                  {run.trigger === "manual" ? "von Hand" : "geplant"}
                </span>
              </span>
              <span className="text-text-secondary">
                {run.items_valued} bewertet · {run.items_skipped} übersprungen
                {run.items_failed > 0 && ` · ${run.items_failed} fehlgeschlagen`}
                {run.status === "running" && " · läuft"}
              </span>
            </button>
            {openRunId === run.id && <RunDetail runId={run.id} />}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Register the route**

In `frontend/src/App.jsx` bei den anderen `lazy`-Importen:

```jsx
const ValuationLog = lazy(() => import("./pages/ValuationLog"));
```

und bei den Routen, nach `inventar`:

```jsx
              <Route path="protokoll" element={<ValuationLog />} />
```

- [ ] **Step 3: Verify**

```bash
npm run lint
npm run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ValuationLog.jsx frontend/src/App.jsx
git commit -m "feat(inventory): a page that says what each run checked and what it found"
```

---

### Task 10: Frontend — Dubletten-Hinweis beim Einbuchen

**Files:**
- Modify: `frontend/src/pages/Inventar.jsx` (Add-Formular)

**Interfaces:**
- Consumes: `api.lookupInventory` (Task 8), `api.updateInventory` (vorhanden)
- Produces: keine

- [ ] **Step 1: Add the lookup to the add form**

In `frontend/src/pages/Inventar.jsx`, im Add-Modal. Zustand für den Treffer:

```jsx
  const [duplicates, setDuplicates] = useState([]);
```

Abfrage beim Verlassen des Setnummer-Feldes (`onBlur` am Eingabefeld für `set_number`):

```jsx
    onBlur={async (e) => {
      const value = e.target.value.trim();
      if (!value) { setDuplicates([]); return; }
      try {
        setDuplicates(await api.lookupInventory(value));
      } catch {
        // Der Hinweis ist eine Hilfe, kein Tor: faellt die Abfrage aus,
        // laesst sich trotzdem einbuchen.
        setDuplicates([]);
      }
    }}
```

Der Hinweis direkt unter dem Feld:

```jsx
  {duplicates.length > 0 && (
    <div className="mt-2 p-3 rounded-lg border border-lego-yellow/40 bg-lego-yellow/5 text-xs">
      <p className="mb-2">
        <strong>{duplicates[0].set_number}</strong> liegt bereits{" "}
        {duplicates.reduce((sum, d) => sum + (d.quantity || 1), 0)}× im Bestand
        {" "}({duplicates
          .map((d) => `${new Date(d.buy_date).toLocaleDateString("de-DE")}, ${d.buy_price} €`)
          .join(" · ")}).
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="px-2 py-1 rounded bg-lego-yellow text-bg-primary font-medium"
          onClick={() => {
            const target = duplicates[0];
            editMutation.mutate({
              id: target.id,
              data: { quantity: (target.quantity || 1) + 1 },
              photoFiles: [],
              deletedPhotoIds: [],
            });
            setDuplicates([]);
            closeAddModal();
          }}
        >
          Menge erhöhen
        </button>
        <button
          type="button"
          className="px-2 py-1 rounded border border-bg-hover"
          onClick={() => setDuplicates([])}
        >
          Trotzdem neu anlegen
        </button>
      </div>
    </div>
  )}
```

`closeAddModal` um `setDuplicates([])` ergänzen, damit der Hinweis nicht im nächsten Dialog stehen bleibt.

- [ ] **Step 2: Verify**

```bash
npm run lint
npm run build
```

Von Hand prüfen: Setnummer eines vorhandenen Sets eingeben, Feld verlassen → Hinweis erscheint; „Menge erhöhen" zählt am vorhandenen Eintrag hoch; „Trotzdem neu anlegen" blendet den Hinweis aus und lässt das Formular unverändert.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Inventar.jsx
git commit -m "feat(inventory): warn about a set already held, and offer to raise its count"
```

---

### Task 11: Frontend — Referenz-Links auf jeder Karte

**Files:**
- Create: `frontend/src/pages/inventoryLinks.js`
- Modify: `frontend/src/pages/Inventar.jsx` (Karte + Bearbeiten-Dialog)

**Interfaces:**
- Consumes: `reference_url` aus der Inventar-Antwort (Task 7)
- Produces: `referenceLinks(item) -> [{label, href}]`

- [ ] **Step 1: Write the link builder**

Neue Datei `frontend/src/pages/inventoryLinks.js`:

```js
// Beide Links entstehen aus der Setnummer und brauchen deshalb keinen
// Speicher. Idealo ist bewusst nur ein Link: als Quelle antwortet die Seite
// vom Server aus in etwa einem von sechs Faellen und liefert dann Preise
// fremder Produkte von der Suchseite.
export function referenceLinks(item) {
  const links = [
    {
      label: "BrickMerge",
      href: `https://www.brickmerge.de/?find=${encodeURIComponent(item.set_number)}`,
    },
    {
      label: "Idealo",
      href:
        "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=" +
        encodeURIComponent(`LEGO ${item.set_number}`),
    },
  ];
  if (item.reference_url) {
    links.push({ label: "Eigener Link", href: item.reference_url });
  }
  return links;
}
```

- [ ] **Step 2: Render them on the card**

In `frontend/src/pages/Inventar.jsx` importieren und in der Karte — neben `Gekauft: … · Tage · Plattform` — ergänzen:

```jsx
  {referenceLinks(item).map((link) => (
    <a
      key={link.label}
      href={link.href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-lego-yellow hover:underline"
    >
      {link.label} ↗
    </a>
  ))}
```

- [ ] **Step 3: Add the field to the edit dialog**

Im Bearbeiten-Dialog, bei den anderen Textfeldern:

```jsx
  <label className="block text-xs text-text-muted mb-1">Eigener Referenz-Link</label>
  <input
    type="url"
    value={editForm.reference_url || ""}
    onChange={(e) => setEditForm({ ...editForm, reference_url: e.target.value })}
    placeholder="https://…"
    className="w-full px-3 py-2 rounded-lg bg-bg-primary border border-bg-hover text-sm"
  />
```

Damit das Feld gefüllt startet und gespeichert wird, an zwei Stellen ergänzen:

- Beim Öffnen des Bearbeiten-Dialogs, wo `setEditForm({...})` aus dem Item
  befüllt wird, `reference_url: item.reference_url || ""` aufnehmen.
- Im Objekt, das `editMutation.mutate({ id, data: {...} })` als `data`
  übergibt, `reference_url: editForm.reference_url || null` aufnehmen —
  ein leeres Feld muss `null` senden, sonst speichert es den leeren String
  und die Karte zeigt einen dritten Link ins Nichts.

- [ ] **Step 4: Verify**

```bash
npm run lint
npm run build
```

Von Hand: Karte zeigt BrickMerge und Idealo; nach Eintrag eines eigenen Links erscheint der dritte.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/inventoryLinks.js frontend/src/pages/Inventar.jsx
git commit -m "feat(inventory): put BrickMerge and Idealo one click from every card"
```

---

## Abschluss

Nach Task 11:

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q && ./.venv/Scripts/python.exe -m ruff check .
cd ../frontend && npm run lint && npm run build
```

Vor dem Merge auf `main`: Die Migration `d3a91c2f80b7` läuft beim Deploy automatisch (`alembic upgrade head` zwischen Build und Container-Tausch). Nach dem Deploy einmal von Hand prüfen, dass der erste Lauf Zeilen schreibt:

```sql
SELECT status, items_total, items_valued, items_skipped FROM valuation_runs ORDER BY started_at DESC LIMIT 3;
SELECT reason, count(*) FROM valuation_run_items GROUP BY reason ORDER BY 2 DESC;
```

Erwartung nach dem Encoding-Fix: `single_source` geht deutlich zurück, weil BrickEconomy wieder liefert. Bleibt der Anteil hoch, steht in `sources` je Set, welche Quelle warum ausfiel — genau die Diagnose, die vorher fehlte.
