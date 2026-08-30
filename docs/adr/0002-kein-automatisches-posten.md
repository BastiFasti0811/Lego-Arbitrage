# Verkaufsweg: kein automatisches Posten

Das System stellt nie selbst Anzeigen ein, ändert und löscht keine. Es erzeugt Texte und Preisvorschläge; der Mensch führt aus und pflegt Status und Anzeigen-URL zurück. Damit ist die in STATUS.md §7 offene Verkaufsweg-Entscheidung getroffen: manuell mit maximaler Vorarbeit.

Gründe: Kleinanzeigen hat keine offizielle API — Automatisierung wäre Browser-Automation gegen die ToS mit Konto-Risiko. Die eBay-Sell-API lohnt den OAuth- und Pflegeaufwand bei privatem Volumen nicht. Und ohne Posting-Automatik entfallen gespeicherte Verkaufs-Credentials komplett (die bisherigen Klartext-Felder in `app_settings` werden entfernt). Folge: Tages-Check und Telegram erinnern, statt zu handeln. (Entschieden 2026-08-29, Nutzervorgabe.)
