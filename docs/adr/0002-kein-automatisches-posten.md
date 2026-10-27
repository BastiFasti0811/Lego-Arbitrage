# Verkaufsweg: kein automatisches Posten

Das System stellt nie selbst Anzeigen ein, ändert und löscht keine. Es erzeugt Texte und Preisvorschläge; der Mensch führt aus und pflegt Status und Anzeigen-URL zurück. Damit ist die in STATUS.md §7 offene Verkaufsweg-Entscheidung getroffen: manuell mit maximaler Vorarbeit.

Gründe: Kleinanzeigen hat keine offizielle API — Automatisierung wäre Browser-Automation gegen die ToS mit Konto-Risiko. Die eBay-Sell-API lohnt den OAuth- und Pflegeaufwand bei privatem Volumen nicht. Und ohne Posting-Automatik entfallen gespeicherte Verkaufs-Credentials komplett (die bisherigen Klartext-Felder in `app_settings` werden entfernt). Folge: Tages-Check und Telegram erinnern, statt zu handeln. (Entschieden 2026-08-29, Nutzervorgabe.)

## Nachtrag 2026-10-26: Kleinanzeigen per Browser

Sebastian hat am 25.10.2026 entschieden, dass Agenten Kleinanzeigen-Inserate „im besten Fall“ selbst einstellen. Für Kleinanzeigen ist der erste Satz oben damit aufgehoben; für eBay gilt er weiter. Das AGB- und Konto-Risiko ist bekannt, Sebastian trägt es. Darum ist der Ablauf auf „fällt möglichst wenig auf“ gebaut:

- **Nur Sebastians echte, eingeloggte Chrome-Sitzung auf victus.** Keine Zugangsdaten in Code, Tickets oder Notizen, kein Login-Skript. Zeigt Kleinanzeigen eine Login-Seite oder ein CAPTCHA, bricht der Agent ab und meldet es.
- **Menschliches Tempo:** höchstens 3 neue Inserate pro Berliner Kalendertag, mindestens 20 Minuten zwischen zwei Inseraten, Pausen zwischen den Formularschritten. Kein Massenbetrieb.
- **Freigabe je Anzeige:** Die ersten 3 Inserate gehen nur nach Sebastians Klick in der App raus. Danach kann er per Einstellung `ka_autopost_without_approval` auf „ohne Freigabe“ schalten; Tageslimit und Abstand bleiben.
- **Löschen nie automatisch.** Es gibt dafür weder Aktion noch Endpunkt noch Befehl.
- **Testlauf:** Formular füllen und bei Kleinanzeigen als Entwurf speichern, nicht veröffentlichen. Braucht keine Freigabe, zählt nicht gegen das Tageslimit. Den Entwurf löscht Sebastian selbst.

Umsetzung: Das Backend hält Auftrag, Freigabe und Leitplanken (`listing_posting_jobs`, `app/services/ka_posting.py`) und erzwingt sie im Code. Den Browser fährt ein Agent über den Skill `kleinanzeigen-einstellen` und die Befehle in `app/tools/ka_posting.py`; er bekommt einen Auftrag nur, wenn Freigabe und Tempo passen. Preis ändern (Preistreppe) und Neu-Einstellen kommen später als eigene Aktionen dazu, mit denselben Grenzen.
