# Design: Inventar für alles + Listing-Lifecycle

Stand 2026-08-30, erarbeitet in Brainstorming- und Grilling-Session. Begriffe: [CONTEXT.md](../../../CONTEXT.md). Grundsatzentscheidungen: [ADR 0001](../../adr/0001-ein-inventar-fuer-alle-artikelarten.md) (ein Inventar für alle Artikelarten), [ADR 0002](../../adr/0002-kein-automatisches-posten.md) (kein automatisches Posten).

## Ziel

Der komplette eigene Bestand — Lego wie Nicht-Lego — wird inventarisiert und beim Verkauf begleitet: Fotos hochladen, KI erkennt den Artikel und schreibt Anzeigentexte für Kleinanzeigen und eBay, ein täglicher Check schlägt Preis-Anpassungen vor, und pro Artikel ist sichtbar, wo er eingestellt ist (mit Link zur eigenen Anzeige). Das System postet nie selbst; der Mensch stellt ein und pflegt den Status zurück.

## Datenmodell

### Änderungen an `InventoryItem`

- `item_type`: `"LEGO" | "GENERIC"` (String). Steuert die Preisquellen-Mechanik. Bestand wird per Migration auf `LEGO` gesetzt.
- `set_number` wird nullable (bei GENERIC leer). `set_name` bleibt als Spalte und dient als allgemeiner Artikelname.
- `buy_price` wird nullable (Dachbodenfund hat keinen Kaufpreis). Alle ROI- und Invest-Rechnungen überspringen Artikel ohne Kaufpreis — kein 0-€-Default, der Statistiken verzerrt.
- `product_group` (Warengruppe): String für Auswertung und Filter. Lego-Artikel automatisch „Lego" (auch als Backfill). Startliste: Lego, Elektronik, Kleidung, Haushalt, Spielzeug, Diverses. UI: Dropdown aus vorhandenen Werten plus Freitext für neue Gruppen. Die KI wählt beim Entwurf aus der vorhandenen Liste (verhindert „Elektro" neben „Elektronik").
- `search_query`: eBay-Suchbegriff für die Preisrecherche. Bei LEGO automatisch aus der Set-Nummer erzeugt, bei GENERIC KI-Vorschlag; immer editierbar.
- `ai_price_min`, `ai_price_max`, `ai_analysis_at`: KI-Preisrahmen der letzten Analyse.
- Neuer Item-Status `DRAFT` (zusätzlich zu HOLDING/SOLD) für die Foto-first-Anlage. DRAFT-Artikel tauchen in Statistik und Bewertung nicht auf; Abbruch = löschen.
- `quantity` (bestehend): Ein Listing bezieht sich immer auf den ganzen Posten. Teilverkauf gibt es nicht; stattdessen Aktion **„Posten teilen"**: neuer Artikel mit kopierten Feldern und kopierten Foto-Dateien, `quantity` wird aufgeteilt (z. B. 3 → 2+1). Kaufpreis und Versandkosten sind Zeilen-Gesamtwerte und werden beim Teilen centgenau anteilig aufgeteilt — die Summe über beide Zeilen bleibt exakt erhalten, sonst zählte das Portfolio investiertes Kapital doppelt. Der Marktpreis-Schnappschuss gilt je Stück und wird kopiert.

### Neue Tabelle `listings`

Eine Zeile je Anzeige-Lebenszyklus eines Artikels auf einer Plattform.

- `item_id` (FK), `platform` (String: `"KLEINANZEIGEN" | "EBAY"`; bewusst kein DB-Enum — Vinted & Co. später ohne Migration).
- `status`: `DRAFT | ACTIVE | PAUSED | ENDED | SOLD`.
- **Constraint:** je (item, platform) höchstens eine Zeile mit Status in (DRAFT, ACTIVE, PAUSED) — partieller Unique-Index. Beendete Zeilen (ENDED/SOLD) sammeln sich als Historie; der Kleinanzeigen-Refresh („löschen + neu einstellen") erzeugt eine neue Zeile.
- Inhalt: `title`, `body` (generierter Anzeigentext, Plaintext), `platform_category` (KI-Vorschlag, z. B. „Elektronik > Audio & Hifi"), `price_type` (`FIXED | VB`; Default VB bei Kleinanzeigen, FIXED bei eBay — beeinflusst nur den Text, nicht die Anpassungslogik).
- Einstell-Daten: `listed_at`, `current_price`, `url` (Link zur eigenen Anzeige; Paste-Feld im Markieren-Formular, nachtragbar; macht Badges und Telegram-Links klickbar).
- Anpassungsregel: `check_interval_days` (Default 14), `price_drop_percent` (Default 10), `min_price` (**Pflicht bei Aktivierung**, vorbefüllt mit 70 % des Startpreises, frei änderbar).
- Steuerung: `next_check_at`, `snoozed_until` („Später" = +3 Tage, fest verdrahtet), `floor_notified_at` (Schmerzgrenze-Hinweis kommt genau einmal).
- Offener Vorschlag: `suggested_price`, `suggestion_reason`, `suggestion_at` (NULL = keiner).

### Neue Tabelle `listing_price_changes`

`listing_id`, `changed_at`, `old_price`, `new_price` — füttert die Historien-Zeitleiste im Artikel-Detail.

## KI-Anbindung (`backend/app/ai/`)

Schmales Provider-Interface, damit später OpenRouter o. ä. als zweite Implementierung dahinter passt:

- `analyze_photos(fotos, hinweise) → ItemDraft`: Name, Warengruppe (aus vorhandener Liste), Zustand, Beschreibung, `search_query`, Plattform-Kategorie, Preisrahmen (min/max), Konfidenz.
- `write_listing(artikel, plattform, preis, price_type) → {title, body}`: deutsche Anzeigentexte, Titel-Limits je Plattform, Plaintext.

Erster Provider: Claude (anthropic-SDK, Vision, structured output). Konfiguration über `.env`: `AI_PROVIDER`, `AI_MODEL` (Default wird bei der Implementierung anhand der aktuellen Modellreferenz festgelegt), `ANTHROPIC_API_KEY`. **Kein Key in `app_settings`** — die bestehenden Klartext-Credential-Felder dort werden im Zuge von PR 1 entfernt (ADR 0002).

Fotos werden vor dem API-Call serverseitig verkleinert (neue Dependency: Pillow). Beim Upload gibt es ein optionales Freitextfeld „Hinweise" (z. B. „Größe M, Rechnung vorhanden"), das in die Analyse einfließt. Ein Anlege-Vorgang beschreibt genau **einen** Artikel; Konvolut-Erkennung (mehrere Gegenstände auf einem Foto) ist bewusst nicht in v1.

## eBay-Preisrecherche für freie Suchbegriffe

`EbaySoldScraper` wird verallgemeinert: Die Such-URL-Erzeugung nimmt einen Query-String statt des fest verdrahteten „LEGO {set_number}"-Templates; der Lego-Pfad ruft sie mit dem bisherigen Template auf. Median, Ausreißerfilter und der Fallback auf aktive BIN-Listings bleiben unverändert.

## Anlege-Flow (Foto-first)

1. „Artikel per Foto anlegen" → Artikel entsteht als DRAFT, Fotos laufen über die bestehende Foto-Infrastruktur.
2. Analyse: KI-Entwurf und eBay-Sold-Median (über `search_query`) parallel.
3. Vorbefülltes Formular mit beiden Preissignalen; korrigieren, speichern → HOLDING.
4. Je Plattform: „Text erstellen" → Listing-Zeile als DRAFT mit Titel/Text/Plattform-Kategorie. Kopieren, von Hand einstellen, dann „Als eingestellt markieren": Mini-Formular mit Preis (vorbefüllt), Datum (heute), URL-Paste-Feld → Status ACTIVE, `next_check_at` = `listed_at` + Intervall.

Der klassische Formular-Weg (ohne Foto/KI) bleibt bestehen — auch für GENERIC.

## Tages-Check (Celery Beat, täglich 08:00 Europe/Berlin)

Kein häufigeres Scanning. Der Lauf bearbeitet ACTIVE-Listings mit erreichtem `next_check_at`, ohne offenen Vorschlag und ohne aktiven Snooze:

1. **Zeitregel:** Vorschlag = `current_price` × (1 − `price_drop_percent`), gerundet (unter 50 € auf volle 1 €, ab 50 € auf volle 5 €), nie unter `min_price`.
2. **Marktabgleich:** Frischer eBay-Sold-Median über `search_query`. Bei LEGO wird der Pipeline-Preis bevorzugt, wenn er jünger als 7 Tage ist, sonst eBay-Sold live. Ist der Median belastbar (≥ 5 Verkäufe) und liegt unter 90 % des Zeitregel-Preises, ist der Vorschlagspreis der Median selbst (gerundet, nicht unter `min_price`); die Begründung nennt beide Zahlen.
3. **Schmerzgrenze:** Steht der Preis auf `min_price` und der Artikel verkauft sich nicht, gibt es genau einen Hinweis (`floor_notified_at`), danach Ruhe; das Dashboard zeigt den Zustand „an der Schmerzgrenze" als Filter.
4. **Verkaufs-Leichen:** Für SOLD-Artikel mit weiteren aktiven Listings erinnert der Lauf, die übrigen Anzeigen zu löschen.
5. **Ausgang:** Telegram-Sammelnachricht („3 Anpassungen fällig: Jacke 45 → 40 € …") mit Anzeigen-URLs; sie enthält neue und weiterhin offene, nicht gesnoozte Vorschläge. Dazu Heartbeat mit Nutzarbeits-Zahlen (geprüft, Vorschläge erzeugt).

## Verkauft-Flow

„Verkauft"-Dialog: echter Erlös (`sell_price`), Plattform. Setzt den Artikel und — falls vorhanden — das verkaufende Listing auf SOLD (Direktverkauf ohne Listing bleibt möglich) und zeigt alle anderen aktiven Listings als Checkliste („Kleinanzeigen-Anzeige noch online — gelöscht?"). Nicht abgehakte bleiben ACTIVE und werden vom Tages-Check angemahnt; Abhaken setzt sie auf ENDED.

## Frontend

- **Inventar:** Filter nach Typ und Warengruppe; pro Karte Plattform-Badges („KA: aktiv seit 12 T · eBay: —"), klickbar bei hinterlegter URL; Einstieg „Artikel per Foto anlegen".
- **Artikel-Detail:** Listing-Panel je Plattform (Status — manuell änderbar, z. B. PAUSED bei pausierter Anzeige —, Text kopieren, Regeln, offener Vorschlag, KI-Text-Refresh-Button on demand), darunter Historien-Zeitleiste aus Listing-Zeilen und `listing_price_changes` („12.05. eingestellt 50 € → 26.05. gesenkt 45 € → 03.06. beendet"). Aktionen: „Posten teilen", „Neu bewerten" (on demand — ungelistete GENERIC-Artikel werden nie automatisch neu bewertet).
- **Fällige Anpassungen:** eigener Bereich mit Menü-Badge; je Vorschlag alt/neu und Begründung, Buttons Übernommen (setzt `current_price`, schreibt Price-Change-Event, plant `next_check_at` neu) / Später (Snooze 3 Tage) / Verwerfen (plant nur neu).
- **Statistik:** Bestands-Auswertung gruppiert nach Warengruppe (Wert, investiert, verkauft, Gewinn); Artikel ohne Kaufpreis sind aus Invest-/ROI-Summen ausgenommen.

## Nicht im Scope (v1)

Auto-Posting (ADR 0002), Vinted und weitere Plattformen, Konvolut-Erkennung, automatischer Erreichbarkeits-Check der Anzeigen-URLs (Datenbasis liegt bereit), automatische Neubewertung ungelisteter Artikel, Teilverkauf eines Postens.

## Migration + Tests

- Alembic: neue Spalten, `listings`, `listing_price_changes`; Backfill `item_type='LEGO'` und `product_group='Lego'`. Der Migrationstest fügt vor dem Upgrade Zeilen ein und prüft den Backfill — CI fährt sonst nur gegen eine leere DB.
- KI-Provider in Tests gemockt; eBay-Freitext-Suche gegen HTML-Fixtures; Tages-Check mit gemocktem Telegram (Vorschlag, Schmerzgrenze-Einmaligkeit, Snooze, Leichen-Erinnerung); Split-Endpoint inkl. kopierter Foto-Dateien; Statistik mit und ohne Kaufpreis.

## Umsetzung: 3 PRs

1. **Datenmodell + manueller Listing-Status:** Migration, Listing-CRUD, Markieren-/Verkauft-Flow mit Checkliste, Badges, Historie, „Posten teilen", Warengruppen-Feld + Filter, Entfernen der toten Klartext-Credential-Felder. Danach ist bereits sichtbar, was wo eingestellt ist.
2. **KI + Foto-first:** `app/ai/` mit Claude-Provider, Foto-Analyse-Flow, eBay-Freitext-Recherche, Textgenerierung, Pillow.
3. **Tages-Check + Anpassungs-UI:** Beat-Task, Telegram-Sammelnachricht, „Fällige Anpassungen", Statistik je Warengruppe.

**Betrieb:** `ANTHROPIC_API_KEY` muss einmalig in die Server-`.env` (macht der Betreiber; Doku-Zeile in `docs/deploy.md` kommt mit PR 2).
