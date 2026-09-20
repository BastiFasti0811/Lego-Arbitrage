---
name: lego-eingang
description: Fotos aus Eingang/neu zu Inventarposten machen. Use when Sebastian sagt "/lego-eingang", "neue Fotos sind drin", "nimm den Eingang", "leg die Sachen an" oder Fotos in Eingang/neu liegen und ins Inventar sollen. Erkennt die Artikel, gleicht sie gegen Prod-Inventar und die eigenen Anzeigen ab, legt sie nach Freigabe an und hinterlegt Anzeigentexte als Entwurf.
---

# Eingang: Fotos zu Inventarposten

Zweiter Weg ins Inventar neben der Foto-first-Anlage in der App. Spec: `docs/superpowers/specs/2026-09-20-eingang-workflow-design.md`.

**Nichts wird ohne Freigabe geschrieben.** Der Durchgang endet mit einer Tabelle; erst nach Sebastians Ja läuft der Import.

## Die zwei Regeln, die den Rest bestimmen

1. **Lego wird als neu verkauft.** Lego-Posten bekommen `NEW_SEALED`, die Fotos dienen nur der Inventarisierung. Ausnahme: sichtbarer Kartonschaden oder Sebastian sagt ausdrücklich „gebraucht mit meinen Fotos". Kartonschaden kommt als Hinweis in die Notiz **und** in die Tabelle.
2. **Was du nicht sicher erkennst, wird nicht angelegt.** Unlesbare Setnummer, unklare Marke, mögliche Dublette: Zeile in die Tabelle mit Vermerk, Entscheidung bei Sebastian.

## Ablauf

1. **Vorbereiten** (lokal, Backend-venv):
   `backend/.venv/Scripts/python.exe -m app.tools.eingang_prepare Eingang/neu Eingang/arbeit --index Eingang/.verarbeitet.json`
   Packt ZIPs aus, überspringt schon importierte Fotos, verkleinert auf 2000 px ohne Metadaten, schreibt `gruppen.json`.
2. **Sichten**: Jedes Foto in `Eingang/arbeit` mit dem Read-Tool ansehen. Bei mehr als etwa 30 Fotos auf parallele Subagents aufteilen (Datei-Liste, Rückgabe: Artikel, Setnummer, Zustand, Menge, Mängel, Sicherheit). Gruppierung aus `gruppen.json` ist ein Vorschlag — bei Lego über die Setnummer korrigieren, sonst über den Bildinhalt.
3. **Abgleichen**:
   - Prod-Inventar lesen (read-only SQL, siehe unten). Lego über `set_number`, sonst über den Namen.
   - Eigene Anzeigen lesen: Kleinanzeigen „Meine Anzeigen" und eBay-Verkäufercockpit im Browser. Nur lesen.
4. **Tabelle vorlegen**: je Artikel Bezeichnung, Warengruppe, Zustand, Menge, Anzahl Fotos, schon im Inventar, schon inseriert, geplante Aktion. Abweichungen und Unsicherheiten ausdrücklich nennen.
5. **Nach Freigabe importieren**: `manifest.json` schreiben, Fotos daneben legen, Sicherungs-Dump ziehen, Probelauf, dann `--apply`.
6. **Nachhalten**: verarbeitete Fotos nach `Eingang/verarbeitet/<Datum>/` verschieben, `mark_processed` aufrufen, Ergebnis gegen das Manifest prüfen.

## Manifest

```json
{"mark": "Eingang 2026-09-20",
 "items": [{"key": "E01", "item_type": "LEGO", "set_number": "75192", "set_name": "Millennium Falcon",
            "condition": "NEW_SEALED", "quantity": 1, "buy_date": "2026-09-20", "notes": "",
            "photos": ["falcon.jpg"],
            "listings": [{"platform": "KLEINANZEIGEN", "status": "DRAFT", "title": "…", "body": "…",
                          "platform_category": "Spielzeug > Bausteine"}]}]}
```

`item_type` ist `LEGO` (dann ist `set_number` Pflicht, `product_group` wird automatisch „Lego") oder `GENERIC` mit eigener `product_group`. Listings: `DRAFT` für vorbereitete Texte, `ACTIVE` mit `price` und `url` für Anzeigen, die es schon gibt. Titel maximal 120 Zeichen.

## Befehle

Sicherung vor dem ersten Schreibzugriff:
```
ssh lego-prod 'D=/mnt/HC_Volume_105179687/lego-arbitrage/backups/manual; mkdir -p $D; docker exec lego-postgres-prod pg_dump -U lego -d lego_arbitrage --clean --if-exists | gzip > $D/pre-eingang-$(date +%Y%m%d-%H%M%S).sql.gz'
```

Bestand lesen (read-only):
```
ssh lego-prod "docker exec -e PGOPTIONS='-c default_transaction_read_only=on' lego-postgres-prod psql -U lego -d lego_arbitrage --csv -c 'SELECT id, set_number, item_type, product_group, set_name, condition, quantity, status FROM inventory_items ORDER BY id;'"
```

Import (Verzeichnis mit `manifest.json` und Fotos):
```
scp -r <verzeichnis> lego-prod:/tmp/eingang
ssh lego-prod 'docker cp /tmp/eingang lego-api-prod:/tmp/eingang'
ssh lego-prod 'docker exec -e PYTHONPATH=/app -w /app lego-api-prod python -m app.tools.import_inventory /tmp/eingang'          # Probelauf
ssh lego-prod 'docker exec -e PYTHONPATH=/app -w /app lego-api-prod python -m app.tools.import_inventory /tmp/eingang --apply'
ssh lego-prod 'docker exec lego-api-prod rm -rf /tmp/eingang; rm -rf /tmp/eingang'
```

## Grenzen

- Keine Marktpreis-Recherche in diesem Durchgang. Lego bekommt den Marktwert vom Bewertungslauf, bei Nicht-Lego bleibt der Preis leer, bis Sebastian einen nennt.
- Anzeigen werden nie selbst eingestellt (ADR 0002). Entwürfe landen in der App, eingestellt wird von Hand.
- Der Import ist wiederholbar: Der Marker `[<mark> <key>]` in der Notiz verhindert Dubletten. Bei einem Abbruch denselben Aufruf erneut starten.
