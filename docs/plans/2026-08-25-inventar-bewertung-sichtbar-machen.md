# Inventar-Bewertung sichtbar machen: Quellen, Protokoll, Dubletten

## Problem Statement

Das Inventar hält 41 Sets. **Neun tragen einen Marktwert, 32 nicht** — und das System meldet dabei keinen einzigen Fehler. Der Lauf vom 25.08.2026, 16:40 UTC (Prod, `task_heartbeats`):

```
{'updated': 3, 'sell_signals': 2, 'errors': 0}   — 658,5 Sekunden Laufzeit
```

Die Ursache ist eine Kette, die an keiner Stelle laut wird.

**Zwei von drei Preisquellen liefern nichts.** `PRICE_SCRAPERS` führt eBay-Sold, BrickEconomy und BrickMerge. Erhoben am 25.08.2026 gegen die laufende Produktion:

| Quelle | Live-Befund | `price_records` gesamt |
|---|---|---|
| EBAY_SOLD | jede Anfrage `403 Forbidden`, der Aktiv-Fallback landet in eBays Bot-Challenge (`/splashui/challenge`) | 0 |
| BRICKECONOMY | HTTP 200, Antwortkörper unlesbar | **0, seit Projektbeginn** |
| BRICKMERGE | funktioniert | 823 (7 Tage) |

**BrickEconomy scheitert an einer Behauptung über eigene Fähigkeiten.** [base.py:141](../../backend/app/scrapers/base.py) verdrahtet `"Accept-Encoding": "gzip, deflate, br"` fest. Im Worker-Image sind `brotli`, `brotlicffi` und `zstandard` nicht installiert (httpx 0.28.1, geprüft am 25.08.2026). BrickEconomy antwortet mit Brotli, httpx reicht den Körper unentpackt durch, `response.text` liefert Binärmüll. `BeautifulSoup` findet null `a[href*='/set/']`, `_search_set` gibt `None` zurück — und `get_price` steigt danach **ohne eine einzige Logzeile** aus. Die Detailseite wird nie abgerufen; in den Worker-Logs steht ausschließlich der `/search?query=`-Aufruf.

Derselbe Bug ist im BrickMerge-Scraper schon einmal aufgetreten und wurde dort lokal umgangen ([brickmerge.py:55-70](../../backend/app/scrapers/brickmerge.py), `Accept-Encoding: identity`). Die gemeinsame Ursache blieb stehen.

Ein zweiter Befund gehört dazu: Mit Standard-User-Agent und `gzip, deflate` antwortet BrickEconomy `403`. Der rotierende User-Agent bringt die Anfrage durch die Tür, die Kompression zerlegt danach den Inhalt. Beide Bedingungen müssen gleichzeitig halten.

**Eine Quelle reicht nicht — zu Recht.** `is_persistable_consensus()` verlangt zwei unabhängige Quellen und höchstens 30 % Streuung ([market_consensus.py:14](../../backend/app/engine/market_consensus.py)). Ein Ein-Quellen-Wert ist geraten und hätte in `unrealized_profit` und in Verkaufssignalen keinen Platz. Die Regel ist richtig. Aus 38 Sets wird deshalb Folgendes:

```
inventory.consensus_unreliable set_number=43230 sources=1
inventory.consensus_unreliable set_number=40800 sources=1
…
```

**Falsch ist die Stille danach.** Alle vier Aussteige-Stellen in [update_inventory.py:80-104](../../backend/app/tasks/update_inventory.py) sind ein nacktes `continue`. Keine erhöht `errors`. Der Heartbeat meldet `success`, der Watchdog meldet `healthy: True`, und die Oberfläche zeigt eine Leerstelle, die von „noch nicht gelaufen" nicht zu unterscheiden ist. Fünf Monate Ausfall bei BrickEconomy blieben genau deshalb unbemerkt.

**Der Dollarkurs ist eine Konstante von 2026-03.** [brickeconomy.py:13](../../backend/app/scrapers/brickeconomy.py) rechnet mit `USD_TO_EUR = 0.92`. Sobald BrickEconomy wieder liefert, verzerrt dieser Wert jeden zweiten Konsenspreis — und weil er nirgends datiert ist, fällt die Drift niemandem auf.

**Doppeltes Einbuchen ist nicht erkennbar.** `POST /api/inventory/` legt jeden Eintrag an, ohne zu prüfen, ob dieselbe Setnummer schon gehalten wird. Mehrfachbestände sind gewollt — die Oberfläche zeigt `x2`- und `x3`-Marken —, aber versehentliche Doppeleingabe und echter Nachkauf sehen danach identisch aus.

**Der Lauf sprengt bereits sein Zeitlimit.** 658,5 Sekunden gegen `task_time_limit=600` und `task_soft_time_limit=540` ([celery_app.py:35-36](../../backend/app/tasks/celery_app.py)). Der Lauf kam nur durch, weil das Limit im aktuellen Worker-Pool nicht greift. Bei rund 55 Sets läuft er unweigerlich hinein.

Dahinter steht ein strukturelles Problem: **Das System kann sein eigenes Nichtstun nicht melden.** Solange „übersprungen" und „nichts zu tun" dasselbe Signal erzeugen, kostet jeder Quellenausfall Monate, bis er auffällt.

## Solution

**Der gemeinsame Client behauptet nur noch, was er kann.** Die feste `Accept-Encoding`-Zeile in `BaseScraper._get_client()` entfällt. httpx setzt den Header dann selbst — genau auf die Verfahren, für die Decoder installiert sind. Dazu kommt `httpx[brotli]` in die Abhängigkeiten, damit `br` weiterhin angeboten wird und der Header-Fingerabdruck nach Browser aussieht. Der Fehler kann danach strukturell nicht wiederkehren: Angebot und Fähigkeit stammen aus derselben Quelle.

**Unlesbare Antworten werden laut.** `_fetch` prüft, ob die Antwort wie Text aussieht, und wirft sonst. Binärmüll erreicht keinen Parser mehr. `BrickEconomyScraper.get_price` protokolliert seinen `set_not_found`-Ausstieg — heute die einzige stumme Rückgabe im Preis-Pfad.

**Der Dollarkurs kommt von der EZB.** Ein neuer `app/services/fx.py` holt den Tagesreferenzkurs aus dem EZB-XML, legt ihn mit Zeitstempel in `app_settings` ab und liefert 24 Stunden lang aus dem Cache. Fällt der Abruf aus, gilt der letzte bekannte Kurs. Gibt es auch den nicht, wird die Konstante genutzt — und der Preis trägt dann einen Vermerk, der bis ins Protokoll durchschlägt. Geraten wird nicht stillschweigend.

**Jeder Lauf schreibt mit, was er getan hat.** Zwei Tabellen halten Läufe und Ergebnisse je Set fest. Die vier stummen `continue` bekommen je einen Grund, und die Zeile führt mit, **was jede Quelle geliefert hat**. Statt einer Leerstelle steht dort künftig: `BrickEconomy: kein Treffer · eBay Sold: 403 · BrickMerge: 29,74 €`.

**Die Aktualisierung wird von Hand auslösbar und im Zustand sichtbar.** Ein Endpunkt stößt den Celery-Task an und legt vorher den Lauf-Datensatz an. Die Inventar-Seite zeigt den letzten Lauf, den Knopf und den Fortschritt; eine eigene Seite zeigt die Historie mit den Set-Zeilen.

**Dubletten werden gemeldet, nicht verboten.** Ein Lookup-Endpunkt liefert vorhandene Einträge zur Setnummer. Das Formular fragt beim Verlassen des Feldes und bietet zwei Wege an: Menge am vorhandenen Eintrag erhöhen oder als eigenen Eintrag anlegen.

**Referenz-Links entstehen von selbst.** Der BrickMerge-Link wird aus der Setnummer erzeugt und ist damit sofort für alle 41 Sets da. Ein neues Feld `reference_url` überschreibt ihn, wenn die Automatik danebenliegt.

## User Stories

1. Als Betreiber möchte ich sehen, **warum** ein Set keinen Marktwert hat, damit ein Quellenausfall nicht wieder Monate unentdeckt bleibt.
2. Als Betreiber möchte ich die Bewertung von Hand starten, damit ich nach dem Einbuchen nicht bis zu sechs Stunden auf den nächsten Lauf warte.
3. Als Betreiber möchte ich sehen, wann der letzte Lauf war und ob gerade einer läuft, damit ich einen 11-Minuten-Vorgang nicht blind mehrfach anstoße.
4. Als Betreiber möchte ich je Lauf nachlesen, welche Quelle für welches Set was geliefert hat, damit ich einen Ausfall von „Set ist unbekannt" unterscheiden kann.
5. Als Betreiber möchte ich beim Einbuchen gewarnt werden, wenn die Setnummer schon im Bestand liegt, damit ich nicht versehentlich doppelt erfasse.
6. Als Betreiber möchte ich bei einer erkannten Dublette die Menge am vorhandenen Eintrag erhöhen können, damit ein echter Nachkauf nicht als zweiter Eintrag endet.
7. Als Betreiber möchte ich von jeder Inventar-Karte direkt zu BrickMerge springen, damit ich einen gemeldeten Wert gegenprüfen kann.
8. Als Betreiber möchte ich einen eigenen Link hinterlegen können, wenn der erzeugte auf die falsche Seite zeigt.
9. Als Betreiber möchte ich, dass BrickEconomy-Preise mit einem tagesaktuellen Kurs umgerechnet werden, damit der Konsens nicht an einer alten Konstante hängt.
10. Als Betreiber möchte ich, dass ein Lauf mit vielen Übersprungenen als Problem gilt, damit „grün" wieder bedeutet, dass Werte entstehen.

## Datenmodell

### `valuation_runs`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | int, PK | |
| `started_at` | datetime(tz) | |
| `finished_at` | datetime(tz), null | null solange der Lauf läuft |
| `trigger` | str(20) | `manual` \| `scheduled` |
| `status` | str(20) | `running` \| `success` \| `failed` |
| `items_total` / `items_valued` / `items_skipped` / `items_failed` | int | |
| `error` | text, null | Abbruchgrund des ganzen Laufs |

### `valuation_run_items`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | int, PK | |
| `run_id` | int, FK → `valuation_runs.id`, `ON DELETE CASCADE` | |
| `item_id` | int, null | Inventar-Eintrag; null, wenn zwischenzeitlich gelöscht |
| `set_number` | str(20) | |
| `outcome` | str(20) | `valued` \| `skipped` \| `failed` |
| `reason` | str(40), null | `no_prices`, `single_source`, `divergence`, `implausible_price`, `zero_consensus`, `exception` |
| `detail` | text, null | Klartext für die Oberfläche |
| `sources` | JSON | je Quelle: Preis, oder warum keiner kam |
| `consensus_price` | float, null | |

`sources` ist der eigentliche Gewinn: die Diagnose steckt in der Quellenlage, nicht in der Zahl.

### `inventory_items`

Neue Spalte `reference_url` (text, null).

**Aufbewahrung:** Läufe älter als 30 Tage werden am Ende jedes Laufs gelöscht (Cascade räumt die Item-Zeilen mit). Kein eigener Task.

## API

```
POST /api/inventory/valuation/run       → {run_id}; 409 wenn ein Lauf < 30 min läuft
GET  /api/inventory/valuation/runs      → letzte Läufe (Zähler, Zeiten, Auslöser)
GET  /api/inventory/valuation/runs/{id} → Lauf + alle Set-Zeilen
GET  /api/inventory/lookup?set_number=  → vorhandene HOLDING-Einträge zur Setnummer
```

`POST …/run` legt den Lauf mit `status=running` an und stößt danach `celery_app.send_task(...)` an. Der Task übernimmt die vorhandene `run_id`, statt eine eigene anzulegen — so gibt es keinen Zustand „angestoßen, aber unsichtbar".

`GET …/lookup` liefert nur `status = HOLDING`. Verkauftes ist keine Dublette.

## Task

`_update_valuations_async(run_id=None)` bekommt einen Recorder, der je Set eine Zeile schreibt. Beim manuellen Start reicht der Endpunkt seine bereits angelegte `run_id` durch; der Beat-Lauf ruft ohne auf und legt den Datensatz selbst mit `trigger=scheduled` an. Der Rückgabewert wird ehrlich:

```python
{'run_id': 17, 'total': 41, 'valued': 9, 'skipped': 32, 'failed': 0}
```

Das Zeitlimit wird für diesen Task auf 3600 s gehoben (`@celery_app.task(time_limit=3600, soft_time_limit=3300)`). **Nicht parallelisiert:** mehr gleichzeitige Anfragen erhöhen genau das Blockade-Risiko, das eBay gerade vorführt.

Der Watchdog bekommt eine Nutzarbeits-Regel analog zu `evaluate_data_freshness`: Ein Lauf, bei dem über die Hälfte der Sets übersprungen wurde, gilt als Problem. „Grün" soll wieder bedeuten, dass Werte entstehen.

## Frontend

**Inventar-Seite.** Der Satz „Marktwerte werden automatisch alle 6 Stunden aktualisiert" wird zur Statusleiste: letzter Lauf mit Zeitpunkt und Kurzergebnis („25.08. 18:40 — 9 von 41 bewertet, 32 übersprungen"), Knopf *Jetzt aktualisieren*, Link *Protokoll*. Während `status=running` ist der Knopf gesperrt und die Abfrage pollt (`refetchInterval`), bis der Lauf endet. Bei elf Minuten Laufzeit ist die Rückmeldung Teil der Funktion, nicht Beiwerk.

**Protokoll-Seite.** Läufe untereinander mit Zeit, Auslöser und Zählern; je Lauf aufklappbar die Set-Zeilen mit Ergebnis, Grund und Quellenlage.

**Dubletten-Hinweis.** Beim Verlassen des Setnummer-Feldes fragt das Formular `/lookup`. Treffer: „40800 liegt bereits 2× im Bestand (24.08.2026, 7 €)" mit *Menge erhöhen* und *Trotzdem neu anlegen*.

**Referenz-Link.** Auf jeder Karte ein Link — `reference_url`, sonst `brickmerge.de/?find=<setnummer>`. Im Bearbeiten-Dialog ein Feld dafür.

## Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| Antwort nicht dekodierbar | `_fetch` wirft, Scraper protokolliert, Quelle fehlt in der Zeile mit Grund |
| Quelle liefert keinen Preis | Zeile in `sources` mit Grund, kein stiller Ausstieg |
| Nur eine Quelle | `outcome=skipped`, `reason=single_source`, Quellenlage vollständig |
| EZB-Abruf scheitert | letzter Kurs aus `app_settings`; sonst Konstante mit Vermerk in `sources` |
| Task stirbt mitten im Lauf | Lauf bleibt `running`; ein Folgelauf nach 30 min darf starten und markiert den alten als `failed` |
| Zweiter Trigger bei laufendem Lauf | `409` mit der laufenden `run_id` |

## Tests

- `_fetch` mit Brotli-Antwort liefert echtes HTML; mit undekodierbarem Körper wirft es (Regression für den Fünf-Monate-Bug)
- `BrickEconomyScraper.get_price` protokolliert, wenn die Suche nichts findet
- FX: frischer Abruf, Cache-Treffer innerhalb 24 h, Rückfall auf letzten Kurs, Rückfall auf Konstante setzt den Vermerk
- Recorder hält jeden der sechs Gründe fest; Zähler im Rückgabewert stimmen mit den Zeilen überein
- `POST …/run` legt Lauf an; zweiter Aufruf bei laufendem Lauf → 409; Lauf älter als 30 min startet neu
- `GET …/lookup` findet HOLDING, ignoriert SOLD
- Aufräumen löscht Läufe > 30 Tage samt Item-Zeilen

## Migration

Eine Alembic-Revision: zwei Tabellen, eine Spalte. Reines Schema, kein Datenumzug — Tests gegen bestehende Zeilen sind hier nicht nötig.

## Non-Goals (YAGNI)

- **eBay-Sold wiederbeleben.** Der `403` bleibt. Playwright/Stealth oder die offizielle eBay-API sind ein eigener Brocken mit eigener Entscheidung.
- **Ein-Quellen-Werte anzeigen.** Die Zwei-Quellen-Regel bleibt unangetastet. Sichtbar wird der Grund, nicht die geratene Zahl.
- **Mehrere Referenz-Links je Set.** Ein Link, überschreibbar.
- **Parallelisierung der Bewertung.** Erst nötig, wenn das erhöhte Zeitlimit nicht mehr reicht.

## Risiken

**Der Fix bringt nicht für alle 41 Sets einen Wert.** BrickEconomy führt möglicherweise nicht jedes Set — die 40xxx-Beigaben (Tom & Jerry, Buggy der Clown, Luke Skywalker) sind Kandidaten dafür. Mit eBay im Block bleiben diese Sets bei einer Quelle und damit ohne Wert. Der Unterschied nach diesem Umbau ist, dass im Protokoll steht, welcher der beiden Fälle vorliegt.

**Nur 1 der 41 Setnummern existiert in `lego_sets`** (75292). `is_plausible_price` fällt dadurch fast immer auf „keine UVP als Anker" zurück und prüft nichts. Das ist kein Blocker für diesen Umbau, aber der Grund, warum die Plausibilitätsprüfung derzeit wirkungslos ist — vermerkt für später.

**BrickEconomy kann den rotierenden User-Agent künftig blocken.** Der `403` bei Standard-Headern zeigt, dass dort eine Erkennung läuft. Bricht sie durch, meldet das Protokoll es ab sofort am selben Tag statt nach fünf Monaten.
