# Eingang-Workflow: Fotos ablegen, Claude legt an

Stand 2026-09-20. Zweiter Weg ins Inventar, neben der Foto-first-Anlage in der App (PR 2). Der App-Weg braucht einen `ANTHROPIC_API_KEY` und bleibt ungenutzt, solange keiner gesetzt ist; dieser Weg nutzt stattdessen die Claude-Code-Sitzung, die ohnehin da ist.

## Ziel

Sebastian legt Fotos in einen Ordner. In einer Claude-Code-Sitzung startet er `/lego-eingang`. Claude erkennt die Artikel, gleicht sie gegen Inventar und die eigenen Anzeigen ab, legt sie nach Freigabe auf Prod an und hinterlegt Anzeigentexte als Entwurf.

Fertig ist der Durchgang, wenn die Posten in der App stehen, ihre Fotos daran hängen und die verarbeiteten Fotos aus dem Eingang verschwunden sind.

## Entscheidungen (Nutzer, 2026-09-19/20)

| Frage | Entscheidung |
|---|---|
| Auslöser | Manuell: `/lego-eingang` in einer Sitzung. Kein Dienst, kein Zeitplan. |
| Autonomie | Tabelle zur Freigabe, danach anlegen. |
| Lego-Zustand | **Immer `NEW_SEALED`.** Fotos dienen bei Lego nur der Inventarisierung. Ausnahme: sichtbarer Kartonschaden oder Sebastian sagt „verkaufe ich gebraucht mit meinen Fotos". |
| Umfang | Inventar + Anzeigentext-Entwurf (DRAFT-Listing). Keine Marktpreis-Recherche im Durchgang. |
| Zuordnung | Lose Fotos, Claude gruppiert; die Gruppierung steht in der Tabelle und wird dort korrigiert. |
| Schreibpfad | Werkzeug in der App (`app.tools.import_inventory`), getestet und versioniert — kein Wegwerf-Skript. |

## 1. Ordner und Ablauf

```
Eingang/                      # gitignored, liegt neben dem Repo-Inhalt
  neu/                        # hier legt Sebastian Fotos ab (auch ZIPs aus dem Handy-Export)
  arbeit/                     # verkleinerte Sichtungskopien + gruppen.json + herkunft.json,
                              # wird bei jedem prepare-Lauf geleert
  verarbeitet/2026-09-20/     # nach dem Import verschoben, mit manifest.json des Durchgangs
  .verarbeitet.json           # SHA-256 je importiertem Foto
```

Ein Durchgang:

1. `python -m app.tools.eingang_prepare prepare` (lokal, Backend-venv): packt ZIPs aus, überspringt Fotos, deren Hash schon in `.verarbeitet.json` steht, dreht sie nach EXIF, verkleinert auf 2000 px ohne Metadaten und schreibt einen Gruppenvorschlag nach Aufnahmezeit. Das ZIP bleibt als `.zip.verarbeitet` liegen: HEIC, Videos und Belege darin werden nicht ausgepackt und wären beim Löschen verloren.
2. Claude sieht die verkleinerten Fotos an, erkennt Artikel und korrigiert die Gruppierung anhand des Bildinhalts (bei Lego über die Setnummer).
3. Claude liest den Prod-Bestand und die eigenen Anzeigen (Kleinanzeigen, eBay) und gleicht ab.
4. Claude legt die Tabelle vor: je Artikel Bezeichnung, Warengruppe, Zustand, Menge, Fotos, schon im Inventar ja/nein, schon inseriert ja/nein, geplante Aktion.
5. Nach Freigabe schreibt Claude eine `manifest.json` und ruft `app.tools.import_inventory` im API-Container auf.
6. `python -m app.tools.eingang_prepare finish --manifest <verzeichnis>`: merkt sich die Hashes der importierten Fotos und verschiebt sie nach `verarbeitet/<Datum>/`. Fotos zurückgestellter Artikel bleiben im Eingang. Erst hier — bricht der Import ab, sieht der nächste Lauf dieselben Fotos wieder.

## 2. Zwei Spuren

**Lego.** Erkannt wird die Setnummer. Ist sie lesbar, gilt: `item_type=LEGO`, `set_number`, Zustand `NEW_SEALED`, Menge aus der Zahl sichtbarer Kartons. Den Marktwert holt sich die Bewertungs-Pipeline später selbst; der Import trägt keinen ein. Ist die Nummer nicht lesbar, geht der Posten nicht durch, sondern in die Tabelle mit dem Vermerk „Setnummer prüfen". Sichtbarer Kartonschaden wird als Hinweis in die Notiz geschrieben und in der Tabelle hervorgehoben — dann entscheidet Sebastian über den Zustand.

**Alles andere.** Zustand aus den Fotos (`NEW_SEALED`, `NEW_OPEN_BOX`, `USED_COMPLETE`, `USED_INCOMPLETE`, `UNKNOWN`), Warengruppe aus der Liste der App plus den im Bestand schon verwendeten, Mängel in die Notiz. Ohne erkennbare Marke oder Modell bleibt der Posten in der Tabelle stehen statt angelegt zu werden.

**Anzeigentexte.** Claude schreibt Titel (max. 120 Zeichen), Beschreibung und Plattform-Kategorie; der Import legt daraus ein DRAFT-Listing an. Standardplattform ist Kleinanzeigen. Der Preis bleibt leer, wenn keiner bekannt ist — ein Entwurf ohne Preis ist in der App seit PR 2 sauber dargestellt. Für Lego steht der Preis nach dem nächsten Bewertungslauf ohnehin am Posten.

## 3. Das Importwerkzeug

`backend/app/tools/import_inventory.py`, aufgerufen im API-Container:

```
python -m app.tools.import_inventory /tmp/import           # Probelauf
python -m app.tools.import_inventory /tmp/import --apply   # schreibt
```

Es liest `manifest.json` plus die Fotodateien daneben und nutzt die Routen-Funktionen der App (`add_inventory_item`, `upload_inventory_photos`, `create_listing`), damit dieselben Regeln gelten wie im Dashboard. DRAFT-Listings schreibt es direkt über das Modell, weil die App dafür sonst den KI-Weg nimmt.

Jeder Posten trägt in seiner Notiz den Marker `[Eingang <Datum> <Schlüssel>]`. Daran erkennt ein zweiter Lauf, was schon existiert, und überspringt es — auch wenn die Notiz in der App bearbeitet wurde und der Marker nicht mehr vorn steht. Fotos werden nur angehängt, wenn der Posten noch keine hat. Ein laufendes Listing bleibt unangetastet; meldet das Manifest eine inzwischen eingestellte Anzeige, während ein Entwurf offen ist, wird dieser Entwurf aktiviert und behält seinen Text.

Ohne `--apply` wird nichts geschrieben: Der Probelauf prüft die Pydantic-Modelle, die Dateien und den vorhandenen Bestand und gibt aus, was entstehen würde.

## 4. Wenn etwas schiefgeht

| Fall | Verhalten |
|---|---|
| Manifest unvollständig oder Foto fehlt | Abbruch vor dem ersten Schreibzugriff, mit Angabe des Schlüssels |
| Import bricht mittendrin ab | Bereits angelegte Posten bleiben; ein erneuter Lauf setzt hinter dem Marker fort |
| Foto doppelt im Eingang | Hash steht in `.verarbeitet.json`, das Foto wird übersprungen |
| Manifest verletzt eine Regel der App (Zustand, Plattform, Länge, Menge, Fotoformat oder -größe) | Abbruch vor dem ersten Schreibzugriff mit Angabe des Schlüssels |
| Gleichnamige Fotos aus verschiedenen Ordnern | Beide bleiben erhalten, das zweite bekommt ein `_2` |
| Artikel zurückgestellt (nicht angelegt) | Seine Fotos bleiben im Eingang und tauchen im nächsten Durchgang wieder auf |
| Posten existiert schon (gleiche Setnummer) | Kein zweiter Posten; die Tabelle weist ihn als Dublette aus, Sebastian entscheidet über die Menge |
| Prod nicht erreichbar | Der Durchgang endet nach der Tabelle; die `manifest.json` bleibt liegen und lässt sich später anwenden |

Vor dem ersten Schreibzugriff eines Durchgangs zieht Claude einen Datenbank-Dump nach `backups/manual/`, wie beim Import vom 19.09.

## Nicht im Umfang

- Marktpreis-Recherche je Posten (eigener Durchgang, wenn gewünscht).
- Automatisches Einstellen von Anzeigen — ADR 0002 gilt weiter: das System postet nie selbst.
- Zeitgesteuerter Lauf ohne Sebastian.
- Der KI-Weg in der App bleibt unverändert; er greift, sobald ein `ANTHROPIC_API_KEY` gesetzt ist.
