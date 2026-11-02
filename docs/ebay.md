# eBay Sell API: Einrichtung und Betrieb

Entscheidung und Grenzen: [ADR 0002, Nachtrag 2026-11-02](adr/0002-kein-automatisches-posten.md). Begriffe: „eBay-Angebot (API)“ in [CONTEXT.md](../CONTEXT.md).

## Was die App tut

| Schritt | Aufruf | Wann |
|---|---|---|
| Fotos hochladen | Media API `createImageFromFile` | beim Einstellen; unsere Fotos sind nicht öffentlich, eBay braucht EPS-Adressen |
| Artikel anlegen | `createOrReplaceInventoryItem` (SKU `LA-S-<item>-<listing>`, Produktion `LA-P-…`) | beim Einstellen |
| Angebot anlegen/aktualisieren | `createOffer` / `updateOffer`, Festpreis, EBAY_DE, Kategorie 19006 | beim Einstellen |
| Veröffentlichen | `publishOffer` → Listing ACTIVE, URL, Schmerzgrenze 70 % | beim Einstellen |
| Preis ändern | `bulkUpdatePriceQuantity` | Preis im aktiven Listing ändern oder Preisvorschlag übernehmen |
| Beenden | `withdrawOffer` → Listing ENDED | Knopf „Beenden“, oder Posten anderswo verkauft |
| Verkaufsabgleich | Fulfillment `getOrders` (nur lesend) | Celery alle 30 min (:07/:37) |

Eingestellt wird nur auf Klick („In der eBay-Sandbox einstellen“ / „Auf eBay einstellen“ im eBay-Entwurf) oder per `ebay_setup publish`. Ohne Zugangsdaten zeigt die App keinen Knopf, und der Abgleich tut nichts.

Der Preis kommt aus dem Entwurf: `draft_price` rechnet den Zustandsabschlag schon ein, danach greift die Preistreppe aus PR 3 über die Anpassungsvorschläge.

## Was nur Sebastian tun kann (Schrittliste)

Kein Schritt braucht ein Passwort in Chat, Ticket oder Notiz. Alles, was geheim ist, landet nur in `backend/.env` auf victus.

### A. Sandbox (zuerst)

1. **Entwicklerkonto:** auf <https://developer.ebay.com> mit einem eigenen Entwicklerkonto anmelden oder eins anlegen (privat, kein Conuti-Konto).
2. **Keyset Sandbox:** unter „Application Keys“ ein Sandbox-Keyset erzeugen. Daraus brauchst du **App ID (Client ID)** und **Cert ID (Client Secret)**. Die Dev ID braucht die App nicht.
3. **Sandbox-Testnutzer:** im Developer-Portal einen Sandbox-Testnutzer anlegen (Bereich „Sandbox“ / „User Access Tokens“). Mit diesem Nutzer gibst du gleich den Consent; dein echtes Konto bleibt unberührt.
4. **RuName:** unter „User Tokens“ → „Get a Token from eBay via Your Application“ eine *eBay Redirect URL* für die Sandbox anlegen, Typ **OAuth**. Als „auth accepted URL“ reicht die eBay-Standardseite. Den angezeigten **RuName** (sieht aus wie `Vorname-AppName-SBX-abcdef`) notieren; er ist kein Secret.
5. **In `backend/.env` auf victus eintragen** (`/srv/lego-arbitrage/backend/.env`, Rechte 0600):
   ```
   EBAY_ENVIRONMENT=sandbox
   EBAY_CLIENT_ID=<App ID>
   EBAY_CLIENT_SECRET=<Cert ID>
   EBAY_RUNAME=<RuName>
   EBAY_LOCATION_POSTAL_CODE=<PLZ>
   EBAY_LOCATION_CITY=<Ort>
   ```
   PLZ und Ort sind der öffentliche Artikelstandort; eine Straße wird nie übertragen.
6. **Consent-Adresse erzeugen:**
   ```
   cd /srv/lego-arbitrage
   docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm api \
     python -m app.tools.ebay_setup consent-url
   ```
   (Die laufenden Container kennen die neuen Werte erst nach einem Neustart; `run --rm` liest die `.env` frisch.)
7. **Consent geben:** die Adresse im Browser öffnen, **als Sandbox-Testnutzer** anmelden, „Zustimmen“. eBay leitet auf eine Seite weiter; die **komplette Adresse aus der Adresszeile** kopieren. Der Code darin gilt nur ca. 5 Minuten.
8. **Code tauschen** (fragt die Adresse verdeckt ab und schreibt den Refresh-Token in die `.env`, ohne ihn anzuzeigen):
   ```
   docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm \
     -v "$PWD/backend/.env:/ebay.env" api \
     python -m app.tools.ebay_setup exchange --env-file /ebay.env
   ```
9. **Neu starten**, damit API, Worker und Beat den Token lesen: `bash scripts/deploy-prod.sh` (oder `docker compose … up -d`).

Ab hier kann ein Agent weitermachen (Schritt 10 bis 12), du musst nicht dabei sein:

10. `… run --rm api python -m app.tools.ebay_setup setup` schaltet Geschäftsrichtlinien ein und legt drei Richtlinien namens „Lego-Arbitrage“ an (Versand DHL Paket 6,99 €, sofortige Zahlung, keine Rücknahme) sowie den Lagerort. `… ebay_setup status` prüft alles.
11. Testlauf: in der App ein eBay-Entwurf für ein Test-Set anlegen → „In der eBay-Sandbox einstellen“. Erwartet: Listing ACTIVE mit `https://www.sandbox.ebay.com/itm/…`.
12. Wieder beenden: „Beenden“ im Listing (zieht das Angebot zurück). Danach den ENDED-Testeintrag in der App löschen, damit er nicht als „inseriert“ zählt.

### B. Produktion (erst nach Sandbox und deiner Freigabe)

1. **Keyset Production** im Developer-Portal erzeugen. eBay schaltet es erst frei, wenn das Thema *Marketplace Account Deletion* erledigt ist: entweder einen Benachrichtigungs-Endpunkt angeben oder die Ausnahme beantragen (die App speichert keine Käuferdaten außer Verkaufspreis und -datum). Was das Portal dort genau verlangt, habe ich nicht geprüft.
2. **RuName Production** wie in A.4, diesmal für Production.
3. In `backend/.env` die Sandbox-Werte ersetzen: `EBAY_ENVIRONMENT=production`, neue Client-ID, neues Secret, neuer RuName. Den alten `EBAY_REFRESH_TOKEN` löschen.
4. A.6 bis A.9 wiederholen, Consent diesmal **mit deinem echten eBay-Konto**.
5. Hast du in eBay schon Geschäftsrichtlinien (Versand, Zahlung, Rücknahme), nimmt die App sie, wenn es je Art genau eine gibt oder eine „Lego-Arbitrage“ heißt; sonst die IDs als `EBAY_FULFILLMENT_POLICY_ID`, `EBAY_PAYMENT_POLICY_ID`, `EBAY_RETURN_POLICY_ID` eintragen. Ohne Richtlinien legt `ebay_setup setup` sie an.

Der Refresh-Token läuft nach 18 Monaten ab (eBay-Standard); dann A.6 bis A.9 wiederholen. `exchange` meldet das Ablaufdatum.

## Offene Punkte, die erst die Sandbox klärt

- **Pflicht-Merkmale der Kategorie 19006 auf EBAY_DE.** Gesetzt werden `brand`/`mpn` sowie die Merkmale Marke, Produktart, LEGO Set-Nummer und (falls bekannt) LEGO Themenwelt. Fehlt eBay ein Pflichtmerkmal, lehnt `publishOffer` mit Fehler 25002 ab; die App zeigt den Text, SKU und Angebot bleiben gespeichert, und der nächste Versuch nimmt dasselbe Angebot.
- **Zustände:** Neu → `NEW`, Neu geöffnet → `NEW_OTHER`, Gebraucht (auch unvollständig, mit Zustandsbeschreibung) → `USED_EXCELLENT`. Ob 19006 auf ebay.de `NEW_OTHER` annimmt, ist ungeprüft.
- **Versandservice-Code** `DE_DHLPaket` in der angelegten Versand-Richtlinie ist ungeprüft.

## Grenzen

- Nur Lego-Sets mit Setnummer und mindestens einem Foto. Sonstige Artikel bleiben beim Einstellen von Hand.
- Teilverkäufe (Menge > 1, nur ein Teil verkauft) bucht der Abgleich nicht; er meldet sie einmal je neuer Bestellung per Telegram, aufgeteilt wird in der App.
- Scheitert das Zurückziehen eines Angebots, steht das im Log und im Task-Ergebnis; der nächste Lauf versucht es erneut.
- Kleinanzeigen-Anzeigen werden nach einem eBay-Verkauf nie gelöscht, nur gemeldet.
- Kaufen und Bieten gibt es nicht.
