# Lego-Arbitrage — Glossar

Ein System, das Lego-Deals findet und den eigenen Bestand — Lego wie Nicht-Lego — inventarisiert, bewertet und beim Verkauf über Kleinanzeigen und eBay begleitet.

## Language

**Artikel** (`InventoryItem`):
Ein physischer Gegenstand oder Posten gleicher Gegenstände im eigenen Bestand — Lego-Set (`item_type=LEGO`) oder beliebiger anderer Gegenstand (`GENERIC`).
_Avoid_: Item, Produkt, Sache

**Warengruppe** (`product_group`):
Grobe Einteilung des Bestands für Auswertung und Filter (z. B. Lego, Elektronik, Kleidung). Lego-Artikel tragen automatisch „Lego".
_Avoid_: Kategorie (allein), Typ

**Listing**:
Die eigene Anzeige eines Artikels auf genau einer Plattform, mit eigenem Preis, eigener Laufzeit und eigenem Status. Entsteht als Entwurf (DRAFT) mit dem generierten Text und wird beim Einstellen ACTIVE; „nicht eingestellt" heißt: keine Zeile oder nur DRAFT. Beendete Zeilen bleiben als Historie stehen.
_Avoid_: Inserat, Anzeige (im Code), Angebot

**Inserat vorbereiten** (`POST /inventory/{id}/listings/prepare`):
Der einzige Textweg zum Listing: legt den Entwurf mit KI-Text und Preisvorschlag an oder nimmt den vorhandenen. Fällt die KI aus, steht ein Vorlagentext mit Warnung drin. Nach dem Einstellen von Hand genügt die Anzeigen-URL (`…/activate`), dann ist das Listing ACTIVE mit Datum. Als „inseriert" zählt ein Artikel, sobald eines seiner Listings je ein Einstell-Datum hatte.

**Montags-Paket** (`app.tasks.listing_pack`):
Montags 08:00 bereitet das System fünf nicht inserierte Lego-Artikel als Kleinanzeigen-Entwurf vor (Fotos zuerst, dann höchster Preisvorschlag) und schickt Fotos, Titel und Text per Telegram. Eingestellt wird weiter von Hand (ADR 0002).

**Geprüfter Deal**:
Was der Zielzähler als Deal zählt (Ziel: 3 pro Woche): ein Offer mit GO oder GO_STAR (die Engine vergibt beides nur bei mindestens zwei Preisquellen) oder ein Auktionslos mit Gebotsfreigabe (`auction_deals`, nie NEEDS_REVIEW, nie allein auf BrickMerge). CHECK-Fälle zählen nicht und werden nur im Wochen-Digest als Zahl genannt.
_Avoid_: Treffer, Alert

**Wochen-Digest** (`app.tasks.goal_digest`):
Montags 07:50, vor dem Montags-Paket: geprüfte Deals der abgeschlossenen Kalenderwoche (Berlin) gegen die Woche davor, dazu Lego-Artikel inseriert/verkauft gegen das Ziel (30 inseriert, 15 verkauft bis 1.12.2026). Dieselben Zahlen zeigt der Inventar-Kopf (`GET /api/goals/progress`); das Laufprotokoll holt sie mit `python -m app.tools.goal_progress`.

**Offer** (`offers`-Tabelle):
Ein fremdes Verkaufsangebot, das die Arbitrage-Pipeline beobachtet. Nie das eigene — das ist ein Listing.
_Avoid_: Listing (für Fremdes), Deal

**Plattform**:
Externer Marktplatz, auf dem ein Listing lebt (Kleinanzeigen, eBay).
_Avoid_: Portal, Kanal

**Plattform-Kategorie**:
Die Einordnung, die eine Plattform beim Einstellen verlangt (z. B. Kleinanzeigen: „Elektronik > Audio & Hifi"). Teil des KI-Entwurfs; keine Auswertungs-Dimension — das ist die Warengruppe.
_Avoid_: Kategorie (allein)

**Anpassungsvorschlag**:
Vom Tages-Check erzeugter Vorschlag, Preis (und auf Wunsch Text) eines laufenden Listings zu ändern. Wird vom Menschen übernommen, verschoben oder verworfen — nie automatisch ausgeführt.
_Avoid_: Alert, Trigger, Sell-Signal

**Sell-Signal** (`sell_signal_active`):
Bestehender Hinweis am Artikel: „Jetzt lohnt sich das Einstellen" — Marktlage **vor** dem Listing. Abzugrenzen vom Anpassungsvorschlag, der ein **laufendes** Listing betrifft.

**Schmerzgrenze** (`min_price`):
Vom Nutzer je Listing gesetzter Mindestpreis. Kein Anpassungsvorschlag unterschreitet ihn.
_Avoid_: Limit, Floor, Mindestgebot

**Tages-Check**:
Täglicher Celery-Lauf, der fällige Listings prüft und Anpassungsvorschläge erzeugt. Es gibt kein häufigeres Scanning für Listings.
_Avoid_: Scan, Monitoring
