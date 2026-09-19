# PR 2: KI + Foto-first-Anlage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fotos hochladen → Claude erkennt den Artikel und liefert einen strukturierten Entwurf (Name, Warengruppe, Zustand, Beschreibung, eBay-Suchbegriff, Preisrahmen) → eBay-Sold-Median zur Query → vorbefülltes Formular → Artikel wird HOLDING. Dazu KI-generierte Anzeigentexte je Plattform (Listing-DRAFT), on-demand Neubewertung für GENERIC-Artikel und die drei PR-1-Review-Reste.

**Architecture:** Neues Modul `backend/app/ai/` mit Provider-Protocol; erster Provider Claude via `AsyncAnthropic().messages.parse()` (Pydantic-validierte Structured Outputs) + Vision-Blocks (base64). Kein Key in `app_settings` — alles über pydantic-settings/.env. `EbaySoldScraper` bekommt eine query-basierte Preisrecherche als NEUE Methode (der frisch gehärtete Lego-Pfad bleibt unangetastet). Der Foto-first-Flow nutzt den neuen Item-Status DRAFT; „Als eingestellt markieren" aktiviert eine vorhandene Listing-DRAFT-Zeile statt sie zu doppeln.

**Tech Stack:** anthropic ≥ 1.0 (Upgrade von 0.96.0 — risikofrei, bisher null Nutzungen im Code), Pillow (neu), FastAPI/SQLAlchemy/Alembic, React 19 + react-query.

**Spec:** `docs/superpowers/specs/2026-08-30-inventar-fuer-alles-design.md` (Abschnitte KI-Anbindung, eBay-Preisrecherche, Anlege-Flow; PR-3-Themen — Tages-Check, Telegram, Warengruppen-Statistik — bleiben draußen). PR-1-Reste laut Final-Review-Triage: Backend-Guard product_group, search_query-Strip, DRAFT-„0€"-Guard.

## Global Constraints

- `from datetime import UTC, date, datetime` — nie `datetime.UTC`. Neue DateTime-Spalten mit `timezone=True` (`test_model_timezones.py` prüft).
- ruff line-length 120 (E/F/I/N/W/UP); Backend-Kommandos aus `backend/` mit `./.venv/Scripts/python.exe -m …`; Frontend-Gates `npm run lint && npm run build`.
- KI-Aufrufe ausschließlich über das offizielle `anthropic`-SDK (AsyncAnthropic); Modell-Default **`claude-opus-5`**, per Setting `ai_model` überschreibbar; NIEMALS ein anderes Modell hartkodieren. `response.stop_reason == "refusal"` immer prüfen (klare deutsche Fehlermeldung), kein Fallback-Routing auf andere Modelle (deterministischer Backend-Service — bewusste Entscheidung).
- API-Key NIE in `app_settings`, NIE loggen; nur `settings.anthropic_api_key` aus `.env`.
- Deutsche UI-/Fehlertexte; bestehende Tailwind-Klassen.
- **Vor jedem Commit** `git branch --show-current` == `feat/inventar-ki`.
- Commits: `git commit -m "<message>" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"`.
- Alembic: genau EINE neue Revision, `down_revision = "d4e8a12f9c30"` (nach `ls backend/alembic/versions` verifizieren — bei neuerem Head diesen nehmen).
- Foto-first legt **GENERIC**-Artikel an (Ruling: Lego-Sets laufen über den bestehenden Set-Nummer-Flow mit Marktdaten-Autofill; KI-Set-Erkennung ist bewusst nicht v1).

---

### Task 1: Branch, Dependencies, Settings, Betriebs-Doku

**Files:**
- Modify: `backend/pyproject.toml`, `backend/app/config.py`, `backend/.env.example`, `docs/deploy.md`

**Interfaces:**
- Produces: Branch `feat/inventar-ki` von origin/main; installierte Pakete `anthropic>=1.0`, `pillow`; Settings-Felder `ai_provider: str = "claude"`, `ai_model: str = "claude-opus-5"`, `anthropic_api_key: str | None = None` (Task 5 konsumiert genau diese Namen).

- [x] **Step 1: Branch**

```bash
git fetch origin && git checkout -b feat/inventar-ki origin/main && git branch --show-current
```

- [x] **Step 2: Dependencies**

In `backend/pyproject.toml`: `"anthropic>=0.40.0"` → `"anthropic>=1.0"`; unter `# Data Processing` neu `"pillow>=11.0"`. Dann:

```bash
cd backend && uv pip install -U "anthropic>=1.0" "pillow>=11.0" && ./.venv/Scripts/python.exe -c "import anthropic, PIL; print(anthropic.__version__, PIL.__version__)"
```

Erwartung: anthropic 1.x, Pillow 11.x. Danach Gesamtsuite (`./.venv/Scripts/python.exe -m pytest -q`) — muss grün bleiben (das SDK wird bisher nirgends importiert).

- [x] **Step 3: Settings-Felder**

In `backend/app/config.py` in `class Settings` (bei den anderen Secrets, Stil der Nachbarfelder):

```python
    # KI-Anbindung (Foto-Analyse + Anzeigentexte); Key NUR aus .env, nie aus app_settings
    ai_provider: str = "claude"
    ai_model: str = "claude-opus-5"
    anthropic_api_key: str | None = None
```

- [x] **Step 4: Doku**

`backend/.env.example`: Block ergänzen:

```
# KI (Foto-Analyse + Anzeigentexte) — Key anlegen unter console.anthropic.com
ANTHROPIC_API_KEY=
# AI_MODEL=claude-opus-5
```

`docs/deploy.md`: in der `backend/.env`-Aufzählung (~Zeile 101) ergänzen: "and `ANTHROPIC_API_KEY` for the photo-analysis/listing-text AI (PR 2); without it the AI endpoints return 503 and everything else keeps working."

- [x] **Step 5: Verifikation + Commit**

```bash
cd backend && ./.venv/Scripts/python.exe -c "from app.config import settings; print(settings.ai_provider, settings.ai_model, settings.anthropic_api_key is None)" && ./.venv/Scripts/python.exe -m ruff check app tests
```

```bash
git add backend/pyproject.toml backend/app/config.py backend/.env.example docs/deploy.md && git commit -m "chore(ai): anthropic 1.x + Pillow, AI-Settings, Betriebs-Doku" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Migration ai_*-Spalten + Item-Status DRAFT

**Files:**
- Modify: `backend/app/models/inventory.py`
- Create: `backend/alembic/versions/f7c3e91a54d2_ai_felder_und_draft_status.py`
- Modify: `backend/app/api/routes/inventory.py` (`portfolio_summary`, `InventoryResponse` + `_to_response`)
- Test: `backend/tests/test_migration_ai_fields.py`, Ergänzung in `backend/tests/test_inventory_optional_buy_price.py`

**Interfaces:**
- Produces: `InventoryItem.ai_price_min/ai_price_max: float|None`, `ai_analysis_at: datetime|None (tz)`; `InventoryStatus.DRAFT = "DRAFT"`; `InventoryResponse` liefert die drei ai_-Felder; `portfolio_summary` ignoriert DRAFT-Items vollständig (auch in `total_items`).

- [x] **Step 1: Failing Migrationstest** (Muster `test_migration_listings.py`: importlib-Load, Mini-Alt-Tabelle mit id+set_number+set_name+buy_price+buy_date+status, eine Zeile einfügen, `upgrade()`, dann: die drei neuen Spalten existieren, sind nullable, Bestandszeile hat NULL-Werte).
- [x] **Step 2: FAIL verifizieren** (`ModuleNotFoundError`/fehlende Datei).
- [x] **Step 3: Model + Migration**

Model (`inventory.py`, unter `search_query`):

```python
    # KI-Preisrahmen der letzten Foto-Analyse (PR 2)
    ai_price_min: Mapped[float | None] = mapped_column(Float)
    ai_price_max: Mapped[float | None] = mapped_column(Float)
    ai_analysis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

(`Float`, `DateTime` zu den sqlalchemy-Imports; `datetime` ist importiert.) `InventoryStatus`: `DRAFT = "DRAFT"` ergänzen (vor HOLDING). Migration: `op.batch_alter_table("inventory_items")` mit drei `add_column`; `down_revision = "d4e8a12f9c30"`; downgrade droppt die drei.

- [x] **Step 4: Statistik härten** — in `portfolio_summary`: erste Zeile nach dem Laden `items = [i for i in items if i.status != InventoryStatus.DRAFT.value]`. In `InventoryResponse` die drei Felder (`ai_price_min: float | None` …) + Durchreichen in `_to_response`. Test ergänzen (`test_inventory_optional_buy_price.py`): `_item()`-Factory um `ai_price_min=None, ai_price_max=None, ai_analysis_at=None` erweitern; neuer Test `test_portfolio_summary_ignores_drafts` (ein DRAFT-Item in der Fake-Session → taucht in keiner Zahl auf).
- [x] **Step 5: Suite + Lint grün, Commit** `feat(inventory): KI-Preisrahmen-Spalten und DRAFT-Status`

---

### Task 3: PR-1-Reste (drei kleine Härtungen)

**Files:**
- Modify: `backend/app/api/routes/inventory.py` (InventoryAdd-Validator, InventoryUpdate-Validator), `frontend/src/components/ListingManager.jsx`
- Test: Ergänzungen in `backend/tests/test_inventory_generic_validation.py`

**Interfaces:** keine neuen — Härtungen bestehender Verträge.

- [x] **Step 1: Failing Tests** — in `test_inventory_generic_validation.py` der Validator-Test, in einer neuen `backend/tests/test_update_guards.py` der Handler-Test im Fake-Session-Stil (Muster `test_split_item.py::_SplitSession`, hier reicht execute/commit/refresh):

```python
def test_lego_strips_whitespace_search_query():
    item = InventoryAdd(**_payload(item_type="LEGO", set_number="75331", buy_price=10.0, search_query="   "))
    assert item.search_query == "LEGO 75331"
```

```python
async def test_update_rejects_product_group_change_on_lego():
    item = _fake_item(item_type="LEGO", product_group="Lego")
    with pytest.raises(HTTPException) as exc:
        await update_inventory_item(1, InventoryUpdate(product_group="Elektronik"), _Session(item))
    assert exc.value.status_code == 400


async def test_update_allows_product_group_on_generic():
    item = _fake_item(item_type="GENERIC", product_group="Diverses")
    response = await update_inventory_item(1, InventoryUpdate(product_group="Elektronik"), _Session(item))
    assert response.product_group == "Elektronik"
```

(`_fake_item` = vollständiger SimpleNamespace wie `_item()` in `test_inventory_optional_buy_price.py` — kopieren und um `item_type`-Override ergänzen; der GENERIC-Fall braucht auch `photos=[]`, `listings=[]` und die ai_-Felder aus Task 2.)

- [x] **Step 2: Implementieren**
1. Im `InventoryAdd`-Validator: `if not (self.search_query or "").strip(): self.search_query = f"LEGO {self.set_number}"` (statt `if not self.search_query`); zusätzlich am Ende beider Zweige `self.search_query = (self.search_query or "").strip() or None` (bei LEGO ist er nach der Ableitung nie leer).
2. Backend-Guard NUR im Handler `update_inventory_item` — nach `_get_item`, vor dem setattr-Loop:

```python
    if item.item_type == InventoryItemType.LEGO.value and "product_group" in data.model_fields_set:
        raise HTTPException(status_code=400, detail="Warengruppe ist bei Lego-Artikeln fest 'Lego'")
```

(Kein neues Pydantic-Feld — der Handler ist der Guard. Das Frontend sendet den Key für LEGO seit PR 1 ohnehin nicht mehr.)
3. `ListingManager.jsx` OpenListing-Kopfzeile: `{listing.current_price != null ? `${Math.round(listing.current_price)}€` : "—"}` statt unguarded `Math.round`.
- [x] **Step 3: Suite + ruff + FE lint/build grün, Commit** `fix(inventory): PR-1-Reste - product_group-Guard, search_query-Strip, 0-Euro-Anzeige`

---

### Task 4: eBay-Preisrecherche für freie Suchbegriffe

**Files:**
- Modify: `backend/app/scrapers/ebay_sold.py`
- Test: `backend/tests/test_ebay_query_price.py`

**Interfaces:**
- Produces: `EbaySoldScraper.get_price_for_query(query: str) -> ScrapedPrice | None` — Sold-Suche (LH_Complete/LH_Sold/PrefLoc, ohne Zustands-Filter, Query URL-enkodiert), Median+Ausreißerfilter über `_calculate_median`, bei <3 Treffern/Bot-Wall Fallback auf aktive BIN-Listings (`is_reliable=False`); Quelle `"EBAY_SOLD"`/`"EBAY_ACTIVE"` wie gehabt. **Der bestehende Lego-Pfad (`get_price`, `_build_sold_url`) wird nicht angefasst** — der wurde gerade erst gehärtet.

- [x] **Step 1: Failing Tests** — `test_ebay_query_price.py` mit gemocktem `_fetch` (monkeypatch auf die Instanz): (a) Sold-HTML-Fixture (bestehende Karten-Fixture aus `tests/fixtures/` wiederverwenden, sonst minimales `li.s-card`-HTML mit 5 Preisen inline) → Median korrekt, `sold_count == 5`, `is_reliable is True`, `source == "EBAY_SOLD"`; (b) leere Sold-Antwort + BIN-Fixture → `source == "EBAY_ACTIVE"`, `is_reliable is False`; (c) beide leer → `None`; (d) URL-Bau: Query `"Bosch PSB 500"` → `_nkw=Bosch+PSB+500`, kein `LEGO`-Präfix, kein `LH_ItemCondition`.
- [x] **Step 2: FAIL, dann implementieren** — neue Methoden nach dem Lego-Block:

```python
    def _build_query_sold_url(self, query: str) -> str:
        params = (
            f"_nkw={quote_plus(query)}"
            f"&LH_Complete=1&LH_Sold=1&LH_PrefLoc=1&_sop=13&rt=nc"
        )
        return f"{EBAY_BASE}/sch/i.html?{params}"

    def _build_query_active_url(self, query: str) -> str:
        params = f"_nkw={quote_plus(query)}&LH_PrefLoc=1&LH_BIN=1&_sop=15"
        return f"{EBAY_BASE}/sch/i.html?{params}"

    async def get_price_for_query(self, query: str) -> ScrapedPrice | None:
        """Marktpreis fuer einen freien Suchbegriff (GENERIC-Artikel, PR 2)."""
```

Körper analog `get_price`: sold laden → `_extract_sold_prices` → bei Preisen Median/min/max/`is_reliable=len>=5`/notes; sonst Challenge-Log + `_price_from_active_listings`-Analogon mit der Query-BIN-URL (kleine private Hilfsmethode oder Inline). `from urllib.parse import quote_plus` ergänzen. Exceptions wie im Lego-Pfad geloggt und verschluckt → `None`.
- [x] **Step 3: Suite + ruff grün, Commit** `feat(scrapers): eBay-Sold-Median fuer freie Suchbegriffe`

---

### Task 5: KI-Modul `app/ai/`

**Files:**
- Create: `backend/app/ai/__init__.py`, `backend/app/ai/schemas.py`, `backend/app/ai/photo_prep.py`, `backend/app/ai/claude_provider.py`
- Test: `backend/tests/test_ai_schemas_and_prep.py`, `backend/tests/test_claude_provider.py`

**Interfaces (Task 6/7 konsumieren exakt diese):**
- `app.ai.schemas`: `class ItemDraft(BaseModel)`: `name: str`, `product_group: str`, `condition: str`, `description: str`, `search_query: str`, `platform_category: str`, `price_min: float`, `price_max: float`, `confidence: str`; `class ListingText(BaseModel)`: `title: str`, `body: str`, `platform_category: str` (die KI nennt die Plattform-Kategorie beim Textschreiben mit — Task 7 speichert sie an der DRAFT-Zeile; entsprechende Zeile in den `_LISTING_SYSTEM`-Prompt bzw. write_listing-Prompt aufnehmen: "platform_category: passende Kategorie der Zielplattform als Pfad, z. B. 'Elektronik > Audio & Hifi'.")
- `app.ai.photo_prep`: `prepare_photo(path: Path, media_type: str | None) -> tuple[bytes, str]` — gibt (bytes, media_type) zurück; wenn längste Kante > 1600 px ODER Datei > 1,5 MB: mit Pillow auf max 1600 px verkleinert und als JPEG q=85 rekomprimiert (`("…", "image/jpeg")`), sonst Original.
- `app.ai.claude_provider`: `class AIProviderError(Exception)` (Attribut `.detail: str`, deutsch); `class ClaudeProvider` mit `async def analyze_photos(self, photos: list[tuple[bytes, str]], hints: str | None, product_groups: list[str]) -> ItemDraft` und `async def write_listing(self, *, name: str, condition: str, notes: str | None, platform: str, price: float, price_type: str) -> ListingText`
- `app.ai.__init__`: `def get_provider() -> ClaudeProvider` — wirft `AIProviderError("ANTHROPIC_API_KEY fehlt — in backend/.env setzen")` wenn `settings.anthropic_api_key` leer; bei unbekanntem `settings.ai_provider` ebenfalls Fehler.

- [x] **Step 1: Failing Tests schreiben**

`test_ai_schemas_and_prep.py`: ItemDraft/ListingText-Roundtrip (`ItemDraft(**{…}).search_query == …`); `prepare_photo`: mit Pillow im Test ein 2400×1200-JPEG in tmp_path erzeugen → Ergebnis-media_type `image/jpeg` und wiedergeöffnet (`PIL.Image.open(io.BytesIO(out))`) längste Kante ≤ 1600; ein 200×200-PNG < 1,5 MB → Bytes identisch zum Original, media_type `image/png`.

`test_claude_provider.py` (SDK gemockt — kein Netz):

```python
class _FakeParseResponse:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed_output = parsed
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._response
```

Provider mit injiziertem Fake-Client (Konstruktor-Parameter `client=None` → intern `AsyncAnthropic(api_key=…, timeout=90.0, max_retries=1)`, im Test Fake übergeben). Tests: (a) `analyze_photos` gibt das ItemDraft aus `parsed_output` zurück und der Call enthält `model == settings.ai_model`, `output_format is ItemDraft`, erste Content-Blocks vom Typ `image` mit base64-Daten, die Warengruppen-Liste im Text-Prompt; (b) `stop_reason == "refusal"` → `AIProviderError` mit deutscher Meldung; (c) `write_listing` reicht Plattform/Preis/price_type in den Prompt und gibt ListingText zurück; (d) `get_provider()` ohne Key (monkeypatch `settings.anthropic_api_key = None`) → `AIProviderError`.

- [x] **Step 2: FAIL, dann implementieren**

`claude_provider.py` (Kern — Steps exakt so umsetzen):

```python
"""Claude-Anbindung: Foto-Analyse und Anzeigentexte (Spec: KI-Anbindung).

Vertrag mit den Endpoints: wirft ausschliesslich AIProviderError mit
deutscher, UI-tauglicher .detail — nie rohe SDK-Exceptions.
"""

import base64

import anthropic
import structlog

from app.ai.schemas import ItemDraft, ListingText
from app.config import settings

logger = structlog.get_logger()

_ANALYZE_SYSTEM = (
    "Du katalogisierst gebrauchte Gegenstaende fuer den Privatverkauf auf deutschen "
    "Plattformen (Kleinanzeigen, eBay). Du bekommst Fotos EINES Artikels und lieferst "
    "einen nuechternen, ehrlichen Entwurf. Keine Uebertreibungen, keine erfundenen "
    "Markennamen oder Modellnummern — wenn unsicher, schreibe es generisch und setze "
    "confidence auf 'low'. Preise in Euro fuer den deutschen Gebrauchtmarkt."
)

_LISTING_SYSTEM = (
    "Du schreibst deutsche Kleinanzeigen-/eBay-Texte fuer Privatverkaeufe: ehrlich, "
    "konkret, ohne Marketing-Floskeln. Titel maximal 65 Zeichen (eBay 80), "
    "Beschreibung 3-6 kurze Saetze, am Ende 'Versand moeglich.' wenn plausibel. "
    "Keine Emojis."
)


class AIProviderError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class ClaudeProvider:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self._client = client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=90.0, max_retries=1
        )
        self._model = settings.ai_model

    async def analyze_photos(
        self, photos: list[tuple[bytes, str]], hints: str | None, product_groups: list[str]
    ) -> ItemDraft:
        blocks = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(data).decode("utf-8"),
                },
            }
            for data, media_type in photos[:4]
        ]
        prompt = (
            "Analysiere den Artikel auf den Fotos.\n"
            f"Waehle product_group aus genau dieser Liste: {', '.join(product_groups)}.\n"
            "condition: NEW_SEALED, NEW_OPEN, USED_COMPLETE oder USED_INCOMPLETE.\n"
            "search_query: der eBay-Suchbegriff, mit dem man verkaufte Exemplare "
            "dieses Artikels findet (Marke + Modell, ohne Zustand).\n"
            "platform_category: passende Kleinanzeigen-Kategorie als Pfad, "
            "z. B. 'Elektronik > Audio & Hifi'.\n"
            "price_min/price_max: realistischer Verkaufsrahmen in Euro."
        )
        if hints:
            prompt += f"\nHinweise des Verkaeufers: {hints}"
        blocks.append({"type": "text", "text": prompt})
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=2048,
                system=_ANALYZE_SYSTEM,
                messages=[{"role": "user", "content": blocks}],
                output_format=ItemDraft,
            )
        except anthropic.APIStatusError as exc:
            logger.error("ai.analyze_failed", status=exc.status_code)
            raise AIProviderError(f"KI-Analyse fehlgeschlagen (HTTP {exc.status_code})") from exc
        except anthropic.APIConnectionError as exc:
            raise AIProviderError("KI-Dienst nicht erreichbar") from exc
        if response.stop_reason == "refusal":
            raise AIProviderError("Die KI hat die Analyse dieser Fotos abgelehnt")
        return response.parsed_output

    async def write_listing(
        self, *, name: str, condition: str, notes: str | None, platform: str, price: float, price_type: str
    ) -> ListingText:
        price_suffix = " VB" if price_type == "VB" else ""
        prompt = (
            f"Schreibe den Anzeigentext fuer {platform.title()}.\n"
            f"Artikel: {name}\nZustand: {condition}\n"
            f"Preis: {price:.0f} Euro{price_suffix} (in den Text uebernehmen).\n"
        )
        if notes:
            prompt += f"Bekannte Details: {notes}\n"
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=_LISTING_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                output_format=ListingText,
            )
        except anthropic.APIStatusError as exc:
            raise AIProviderError(f"Textgenerierung fehlgeschlagen (HTTP {exc.status_code})") from exc
        except anthropic.APIConnectionError as exc:
            raise AIProviderError("KI-Dienst nicht erreichbar") from exc
        if response.stop_reason == "refusal":
            raise AIProviderError("Die KI hat die Texterstellung abgelehnt")
        return response.parsed_output
```

`photo_prep.py`: Pillow-Import lokal in der Funktion (Import-Kosten), `Image.open` → `im.thumbnail((1600, 1600))` bei Überschreitung, `im.convert("RGB").save(buf, "JPEG", quality=85)`. `__init__.py`: `get_provider()` wie im Interface + Re-Exports (`ItemDraft`, `ListingText`, `AIProviderError`, `prepare_photo`, `get_provider`).

- [x] **Step 3: Suite + ruff grün, Commit** `feat(ai): Claude-Provider mit Foto-Analyse und Anzeigentexten`

---

### Task 6: Draft-/Analyze-/Confirm-/Revalue-Endpoints

**Files:**
- Modify: `backend/app/api/routes/inventory.py`
- Test: `backend/tests/test_ai_endpoints.py`

**Interfaces:**
- `POST /api/inventory/draft` (kein Body) → legt `InventoryItem(item_type=GENERIC, set_name="Neuer Artikel", product_group="Diverses", status=DRAFT, buy_date=date.today())` an → `InventoryResponse`.
- `POST /api/inventory/{item_id}/analyze` Body `{"hints": str | None}` → 400 wenn Item keine Fotos hat; lädt bis zu 4 Foto-Dateien (`_photo_dir`/sort_order, `prepare_photo`), ruft `get_provider().analyze_photos(...)` mit der product-groups-Liste (Startliste ∪ DB wie im `/product-groups`-Endpoint), danach **sequenziell** `EbaySoldScraper().get_price_for_query(draft.search_query)` (die Query entsteht erst in der KI-Antwort — die Spec-Formulierung „parallel" ist damit überholt); persistiert am Item NUR `ai_price_min/ai_price_max/ai_analysis_at (now UTC)`; Response `{"draft": ItemDraft-Dump, "ebay": {"median", "sold_count", "is_reliable", "source_url"} | None}`. `AIProviderError` → HTTP 503 mit `.detail`; fehlender Key ebenfalls 503.
- `POST /api/inventory/{item_id}/confirm` (kein Body) → nur für DRAFT-Items (sonst 400); 400 wenn `set_name` leer/"Neuer Artikel" unverändert UND keine Felder gepflegt — konkret: 400 mit "Bitte erst Artikeldaten speichern" wenn `set_name.strip() in ("", "Neuer Artikel")`; setzt `status = HOLDING` → `InventoryResponse`. (Felder kommen vorher per bestehendem PATCH.)
- `POST /api/inventory/{item_id}/revalue` (kein Body) → nur GENERIC (400 sonst — Lego hat die Pipeline) und nur mit nicht-leerer `search_query` (400 "search_query fehlt"); holt `get_price_for_query`, setzt bei Treffer `current_market_price = round(median, 2)`, `market_price_updated_at = now UTC`, ruft `_recalculate_unrealized_metrics`, committet → `InventoryResponse`; kein Treffer → 404 mit "Keine eBay-Verkaeufe zu dieser Suche gefunden".
- Import-Stil: `from app.ai import AIProviderError, get_provider, prepare_photo` + `ItemDraft` nur für Typen; Scraper-Import wie im Bewertungs-Task.

- [x] **Step 1: Failing Tests** — Fake-Session-Stil (Muster `test_split_item.py::_SplitSession`); Provider + Scraper via `monkeypatch` auf Modulebene in `inventory.py` ersetzen (z. B. `monkeypatch.setattr("app.api.routes.inventory.get_provider", lambda: fake_provider)`). Mindestens: draft-Anlage liefert DRAFT-Status; analyze ohne Fotos → 400; analyze happy path (Fake-Provider liefert ItemDraft, Fake-Query-Preis) → ai_-Felder am Item gesetzt, Response enthält draft+ebay; analyze mit `AIProviderError` → HTTPException 503 und Detail durchgereicht; confirm auf HOLDING-Item → 400; confirm auf DRAFT mit gepflegtem Namen → HOLDING; revalue auf LEGO → 400; revalue GENERIC ohne search_query → 400; revalue happy path setzt current_market_price.
- [x] **Step 2: FAIL, implementieren** — die neuen Routen bei den anderen statischen Routen einsortieren (`POST /draft` direkt neben `/lookup`/`/valuation`, gleiche Vorsicht vor `/{item_id}`-Matching; die drei `/{item_id}/<statisch>`-POSTs sind unkritisch). Foto-Bytes: `(_photo_dir(item.id) / photo.filename)` lesen, `prepare_photo(path, photo.content_type)`; fehlende Dateien überspringen; wenn danach 0 → 400. Im Formular-Schritt zeigt das Frontend auch `draft.platform_category` und `draft.description` an (Task 8) — der Analyse-Response-Dump muss beide enthalten (ItemDraft-Dump tut das automatisch).
- [x] **Step 3: Suite + ruff grün, Commit** `feat(inventory): Foto-first-Endpoints - Draft, KI-Analyse, Confirm, Neubewertung`

---

### Task 7: KI-Anzeigentexte + DRAFT-Listing-Aktivierung

**Files:**
- Modify: `backend/app/api/routes/listings.py`
- Test: Ergänzungen in `backend/tests/test_listing_routes.py`

**Interfaces:**
- `POST /api/inventory/{item_id}/listings/draft` Body `{"platform": str, "price": float | None}` → generiert via `get_provider().write_listing(...)` (name=`item.set_name`, condition=`item.condition`, notes=`item.notes`, price_type=`default_price_type(platform)`, price=Body-Preis oder Fallback `item.current_market_price` → `(ai_price_min+ai_price_max)/2` → `buy_price*1.5` → 400 "Kein Preis ermittelbar — bitte Preis angeben"): existiert offene DRAFT-Zeile der Plattform → Texte überschreiben; existiert ACTIVE/PAUSED → 400 "Schon eingestellt — Text-Refresh nutzen"; sonst neue Zeile `status=DRAFT` mit `price_type`, `title`, `body` und `platform_category` aus dem `ListingText`-Ergebnis. → `ListingResponse`. `AIProviderError` → 503. (refresh-text überschreibt title/body/platform_category ebenso.)
- `POST /api/inventory/{item_id}/listings/{listing_id}/refresh-text` (kein Body) → nur offene Listings; Preis = `current_price` (ACTIVE/PAUSED) sonst Draft-Fallback wie oben; überschreibt title/body → `ListingResponse`; 503 bei Provider-Fehler.
- `create_listing` („Als eingestellt markieren"): wenn die offene Zeile der Plattform ein **DRAFT** ist, wird SIE aktiviert (Preis/listed_at/url/min_price/price_type/next_check_at setzen, `status=ACTIVE`) statt 400; ACTIVE/PAUSED → weiterhin 400. Titel/Body bleiben erhalten.
- Draft-Zeilen sind in `open_listing_responses` bereits enthalten (OPEN_LISTING_STATUSES) — Badges zeigen DRAFT nicht (Frontend filtert ACTIVE/PAUSED, bleibt so).

- [x] **Step 1: Failing Tests** — im bestehenden Fake-Stil: draft-Endpoint legt DRAFT-Zeile mit Texten an (Fake-Provider); draft bei ACTIVE → 400; zweiter draft-Aufruf überschreibt Texte statt neuer Zeile; `create_listing` aktiviert vorhandenes DRAFT (Status ACTIVE, Preis gesetzt, title unverändert); refresh-text auf ENDED → 400; Preis-Fallback-Kette (Item ohne alle Preisquellen → 400).
- [x] **Step 2: FAIL, implementieren** — `from app.ai import AIProviderError, get_provider` + `from app.services.listing_rules import …` (bestehend). Kleine private Hilfe `_draft_price(item, listing, body_price)` für die Fallback-Kette (pure, testbar).
- [x] **Step 3: Suite + ruff grün, Commit** `feat(listings): KI-Anzeigentexte als DRAFT und Aktivierung vorhandener Entwuerfe`

---

### Task 8: Frontend — Foto-first-Flow + KI-Buttons

**Files:**
- Modify: `frontend/src/api/client.js`, `frontend/src/pages/Inventar.jsx`, `frontend/src/components/ListingManager.jsx`
- Create: `frontend/src/components/PhotoFirstModal.jsx`

**Interfaces:**
- client.js (Inventory-Block): `createDraft: () => request("/inventory/draft", { method: "POST" })`, `analyzeItem: (id, hints) => request(`/inventory/${id}/analyze`, { method: "POST", body: JSON.stringify({ hints: hints || null }) })`, `confirmItem: (id) => request(`/inventory/${id}/confirm`, { method: "POST" })`, `revalueItem: (id) => request(`/inventory/${id}/revalue`, { method: "POST" })`, `draftListingText: (itemId, platform, price) => request(`/inventory/${itemId}/listings/draft`, { method: "POST", body: JSON.stringify({ platform, price: price ?? null }) })`, `refreshListingText: (itemId, listingId) => request(`/inventory/${itemId}/listings/${listingId}/refresh-text`, { method: "POST" })`
- `PhotoFirstModal.jsx` (`{ onClose, onCreated }`): 3 Schritte in einem Modal — (1) Foto-Dropzone (Muster `createLocalPhotoEntries`/`revokeLocalPhotoEntries` aus Inventar.jsx als Import oder Kopie der zwei kleinen Helpers, Vorschau-Grid) + „Hinweise (optional)"-Textarea + Button „Analysieren" → `createDraft()` → `uploadInventoryPhotos(id, files)` → `analyzeItem(id, hints)`; Ladezustand „Claude schaut sich die Fotos an…"; Fehler (503-Detail!) sichtbar rot mit „Erneut versuchen"; (2) vorbefülltes Formular (name→set_name, product_group [Dropdown+datalist wie Add-Modal], condition-Select, Beschreibung→notes [Textarea], search_query, buy_price optional, quantity; darüber Preis-Zeile: „KI-Schätzung {price_min}–{price_max} € · eBay-Median {median} € ({sold_count} Verkäufe)" — eBay-Teil nur wenn vorhanden, `is_reliable === false` ergänzt „(wenige Daten)"); (3) „Übernehmen" → `updateInventory(id, felder)` → `confirmItem(id)` → `onCreated()`; „Abbrechen" in Schritt 1/2 → `deleteInventory(id)` (wenn Draft existiert) + `onClose()`.
- Inventar.jsx: zweiter Kopf-Button „Per Foto anlegen" (öffnet PhotoFirstModal; onCreated invalidiert `["inventory"]` + `["productGroups"]`); auf GENERIC-Karten Button „Neu bewerten" (nur wenn `search_query`; `revalueItem`-Mutation, Fehler-Toast wie üblich, invalidiert `["inventory"]`).
- ListingManager.jsx: (a) wenn offenes Listing DRAFT ist: Textblock (title fett, body pre-wrap) + „Text kopieren" (`navigator.clipboard.writeText`) + „Text neu generieren" (`refreshListingText`) + darunter das bestehende `ActivateForm` (aktiviert die DRAFT-Zeile — Backend macht das transparent); (b) wenn KEIN offenes Listing und Artikel nicht SOLD: über dem ActivateForm Button „Text mit KI erstellen" (`draftListingText(item.id, platform, null)`, danach refetch — Zeile erscheint als DRAFT); (c) Fehlerzeile (503-Detail) im Plattform-Block.

- [x] **Step 1: client.js + PhotoFirstModal + Integrationen implementieren** (kein FE-Testrunner; sorgfältig gegen die Backend-Verträge aus Task 6/7 arbeiten — Feldnamen exakt).
- [x] **Step 2: Gates**

```bash
cd frontend && npm run lint && npm run build
```

- [x] **Step 3: Commit** `feat(frontend): Foto-first-Anlage mit Claude-Analyse und KI-Anzeigentexten`

---

### Task 9: Endabnahme + PR

- [ ] **Step 1:** Backend-Suite + ruff + FE lint/build — alles grün; `ls backend/alembic/versions` → genau eine neue Revision hinter `d4e8a12f9c30`.
- [ ] **Step 2:** `git branch --show-current` prüfen, push, PR öffnen (gh-Account BastiFasti0811, danach zurückwechseln): Titel `KI + Foto-first-Anlage (PR 2/3)`, Body: Spec-Verweis, Kernpunkte (Claude-Provider, Foto-first-Flow, eBay-Freitext, DRAFT-Listing-Texte, PR-1-Reste), Hinweis „ANTHROPIC_API_KEY muss vor der Nutzung in backend/.env auf dem Server gesetzt werden — ohne Key: 503 nur auf den KI-Endpoints", Footer `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- [ ] **Step 3:** CI abwarten; Merge erst nach Review-Kette + CI-Beleg und User-Freigabe-Modus wie PR 1.
