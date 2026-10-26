---
name: kleinanzeigen-einstellen
description: Freigegebene Kleinanzeigen-Entwürfe aus der App im eingeloggten Chrome auf victus einstellen (Nachtrag ADR 0002). Use when Sebastian sagt "/kleinanzeigen-einstellen", "stell die freigegebenen Anzeigen ein", "Testlauf Kleinanzeigen", oder ein Autopilot-Lauf Einstell-Aufträge abarbeiten soll. Holt höchstens einen Auftrag pro Durchgang, füllt das Formular in menschlichem Tempo und meldet das Ergebnis zurück. Löscht nie.
---

# Kleinanzeigen per Browser einstellen

Nachtrag zu ADR 0002 (`docs/adr/0002-kein-automatisches-posten.md`). Kleinanzeigen hat keine API; das Einstellen per Browser verstößt gegen die AGB und kann Sebastians Konto kosten. Er trägt das Risiko. Dieser Skill ist darauf gebaut, möglichst wenig aufzufallen. Im Zweifel abbrechen, nicht improvisieren.

## Harte Grenzen

- **Nur Sebastians eingeloggte Sitzung** im Chrome auf victus (AgentsRoom-Chrome, Browser „Chrome · victus“). Niemals einloggen, kein Passwort eingeben, keine Zugangsdaten notieren, die vorbefüllte E-Mail der Login-Seite nicht wiedergeben. Login-Seite oder CAPTCHA → abbrechen (`fail`), Sebastian Bescheid geben.
- **Nie löschen.** Keine Anzeige, keinen Entwurf auf Kleinanzeigen, keine Zeile in der App.
- **Keine Selbstfreigabe.** Freigeben kann nur Sebastian in der App. Der Agent bekommt über `next` nur Aufträge, die freigegeben sind und ins Tempo passen; die Grenzen (3 am Tag, 20 Minuten Abstand, die ersten 3 nur mit Freigabe) erzwingt das Backend.
- **Ein Auftrag pro Durchgang.** Nach einer veröffentlichten Anzeige ist Schluss, auch wenn noch Aufträge warten.
- **Testlauf heißt: nicht veröffentlichen.** Bei `dry_run: true` das Formular füllen, prüfen, Tab schließen. Kein Klick auf „Anzeige aufgeben“/„Veröffentlichen“.
- **Im Zweifel abbrechen.** Unerwartetes Formular, Pflichtfeld, das im Auftrag fehlt, Fehlermeldung von Kleinanzeigen: `fail` mit kurzer Beschreibung, nicht raten.

## Befehle (Prod läuft lokal auf victus)

```
KA='docker exec -e PYTHONPATH=/app -w /app lego-api-prod python -m app.tools.ka_posting'
$KA status                         # Leitplanken und offene Aufträge
$KA request <listing_id> --dry-run # Testlauf anlegen (echte Aufträge legt Sebastian in der App an)
$KA next                           # nächsten freigegebenen Auftrag holen -> IN_PROGRESS, Fotos exportiert
$KA done <job_id> --url <anzeigen-url>   # veröffentlicht: Listing wird ACTIVE
$KA draft-saved <job_id> --note "…"      # Testlauf fertig
$KA fail <job_id> --error "…"            # abgebrochen
```

Jede Ausgabe ist JSON. `next` liefert entweder `{"job": null, "reason": "…"}` (dann ist der Durchgang zu Ende, Grund an Sebastian melden) oder den Auftrag mit `title`, `body`, `price`, `price_type` (`VB`/`Festpreis`), `condition` (Kleinanzeigen-Bezeichnung), `category`, `category_type`, `shipping` und `photos` (Pfade im Container, schon in Reihenfolge, verkleinert, ohne Metadaten).

Fotos auf den Host holen, damit Chrome sie hochladen kann:
```
D=<scratchpad>/ka_job_<job_id>; mkdir -p "$D"
docker cp lego-api-prod:/data/ka_export/job_<job_id>/. "$D"/
```
Nach `done`/`draft-saved`/`fail` räumt das Backend den Export im Container auf; den Host-Ordner selbst löschen.

## Ablauf

1. `$KA status` lesen. Läuft schon ein Auftrag (`IN_PROGRESS`) oder meldet `block` ein Limit, nichts tun und das melden.
2. `$KA next`. Kein Auftrag → Grund melden, fertig.
3. Fotos per `docker cp` holen (siehe oben).
4. Chrome: `chrome_select_browser` auf „Chrome · victus“, neuen Tab öffnen. Nicht Sebastians offene Tabs benutzen.
5. **Sitzung prüfen**: `https://www.kleinanzeigen.de/m-meine-anzeigen.html` öffnen. Landet der Tab auf `login.kleinanzeigen.de` oder zeigt ein CAPTCHA → `$KA fail <job_id> --error "Nicht eingeloggt"` bzw. `"CAPTCHA"`, Tab schließen, Sebastian melden.
6. **Duplikat prüfen**: In „Meine Anzeigen“ nach dem Titel suchen. Gibt es eine aktive Anzeige mit gleichem Titel → nicht einstellen, `fail` mit „Anzeige existiert schon: <URL>“. Sebastian trägt sie dann in der App per URL nach.
7. **Formular** (Stand 26.10.2026 am echten Formular geprüft). Vor jedem Schritt 2–6 Sekunden warten, nicht gleichmäßig. Text mit `chrome_type` und `trusted: true` eintippen, nicht per Script einsetzen. Nur über Selektoren klicken, nie über Bildschirmkoordinaten: Ein Koordinatenklick im Zustandsdialog traf im Testlauf die Zeile darunter.
   - **Kategorie** (Schritt 1, `p-anzeige-aufgeben.html`): Lego = `#cat_17` (Familie, Kind & Baby) → `#cat_23` (Spielzeug) → `#cat_lego_duplo`, dann den Knopf „Weiter“ (`button.button[type=submit]`). „Weiter“ führt nur zu `p-anzeige-aufgeben-schritt2.html`, es veröffentlicht nichts. Andere Artikel: Kategorie nur wählen, wenn `category` eindeutig zu einem Eintrag passt, sonst `fail`.
   - **Fotos**: `chrome_upload_file` auf `input[type=file]` mit allen Host-Pfaden in Reihenfolge (01.jpg zuerst). Danach muss das erste Bild das Etikett „Titelbild“ tragen.
   - **Titel**: `#ad-title` ← `title`.
   - **Zustand**: Knopf mit dem Text „Zustand auswählen“ öffnet einen Dialog. Die Radios sind versteckt, also das Label klicken, dessen `for` auf das Radio mit dem passenden Wert zeigt: `Neu` = `new`, `Sehr Gut` = `like_new`, `Gut` = `ok`, `In Ordnung` = `alright`. Dann „Bestätigen“ (ein `type=button`, sendet nichts ab). Bei `condition: null` das Feld leer lassen, es ist optional.
   - **Beschreibung**: `#ad-description` ← `body`, Zeilenumbrüche bleiben erhalten (Zeichenzähler darunter prüfen).
   - **Versand**: `#ad-shipping-enabled-yes` ist voreingestellt, Versandmethoden aus dem Konto nicht ändern.
   - **Preis**: `#ad-price-amount` ← `price`. Preistyp: `#ad-price-type` öffnen, dann `#ad-price-type-menu-option-0` (Festpreis) oder `-1` (VB). Option `-2` („Zu verschenken“) nie.
   - **Direkt kaufen** (`input[name=buyNow]`): Kleinanzeigen stellt „Ja“ vor. So lassen, bis Sebastian anders entscheidet.
   - **Nicht anfassen**: PLZ, Ort, Straße, Name (kommen aus dem Konto), die kostenpflichtigen Extras (`input[name=articles]`: Highlight, Hochschieben, Top-Anzeige, Galerie) und die Marketing-Einwilligung `#ad-marketing-consent`. Alle drei müssen am Ende leer bzw. unverändert sein.
8. **Kontrolle**: Per `chrome_evaluate` nur lesen und gegen den Auftrag abgleichen: `#ad-title`, `#ad-description`, `#ad-price-amount`, Text von `#ad-price-type`, Anzahl Fotos, `input[name=articles]:checked` (muss 0 sein), `#ad-marketing-consent` (muss false sein). Dazu ein Screenshot. Weicht etwas ab → korrigieren oder `fail`.
9. **Testlauf** (`dry_run: true`): Knopf „Entwurf speichern“, nie „Anzeige aufgeben“. Kleinanzeigen bestätigt mit dem Dialog „Entwurf gespeichert“. Dann `$KA draft-saved <job_id> --note "Entwurf gespeichert, nicht veröffentlicht"`, Tab verlassen (`about:blank`). Den Entwurf nicht löschen; Kleinanzeigen hält höchstens 5 Entwürfe je 60 Tage, Sebastian räumt sie selbst auf. Sind schon 5 da, nicht speichern, sondern `draft-saved` mit „Formular gefüllt, Entwurfsplätze voll“.
10. **Echt** (`dry_run: false`): den Knopf „Anzeige aufgeben“ klicken. Auf die Bestätigungsseite warten, die Anzeigen-URL (`https://www.kleinanzeigen.de/s-anzeige/…`) ablesen, notfalls über „Meine Anzeigen“. Dann `$KA done <job_id> --url <url>`. Ohne URL kein `done`: dann `fail` mit „Veröffentlicht, URL nicht gefunden — in Meine Anzeigen prüfen“.
11. Host-Fotoordner löschen, Ergebnis kurz an Sebastian melden (Titel, Preis, URL oder Grund).

## Wenn etwas hängen bleibt

Ein Auftrag, der länger als 2 Stunden `IN_PROGRESS` steht, wird beim nächsten `next` als FAILED markiert („Ohne Rückmeldung abgebrochen — in „Meine Anzeigen“ prüfen …“). Er wird nicht wiederholt, weil eine halb veröffentlichte Anzeige sonst doppelt online ginge. Sebastian prüft „Meine Anzeigen“ und trägt die URL von Hand ein oder legt einen neuen Auftrag an.

## Später

Preis ändern (Preistreppe) und Neu-Einstellen kommen als eigene Aktionen dazu, mit denselben Grenzen. Bis dahin ändert dieser Skill keine laufende Anzeige.
