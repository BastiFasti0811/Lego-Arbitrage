# STATUS — Lego-Arbitrage (Stand-Rekonstruktion 2026-08-16)

Erhoben am 2026-08-16 durch vollständige Repo-, Git- und Lokal-Laufzeit-Inventur plus adversarialen Gegenblick. Server (`spm-prod-01`) wurde **nicht** betreten — alle Server-Aussagen sind entsprechend markiert.

## 1. Wiedereinstiegs-Briefing

**Wo stehe ich:** Analyse-Tool, Dashboard und Inventar sind gebaut und deployed (Hetzner, Container liefen am 11.08.) — aber die automatische Marktbeobachtung, der Kern des Arbitrage-Ziels, ist **seit 2026-03-25 durch einen Ein-Zeilen-Bug tot** und meldet sich dabei selbst als gesund. Verkauf ist manuell (vorbefüllte Links), Phase 3 (ML/AI) nicht begonnen. Seit 30.06. ruht die Entwicklung.

**Nächste konkrete Handlung:** PR #7 mergen (Scrape-Fix + Worker-Loop-Fix + dieser Report; der Merge deployt automatisch) — und danach die **Watchlist befüllen**: Sie ist leer, ohne Einträge scrapt auch die reparierte Pipeline nichts.

**Die 3 größten Risiken:**
1. **Stiller Datenausfall** *(Fix in PR #7)*: `offers`/`price_records` waren nie befüllt (Server-verifiziert 2026-08-16: 0/0, `last_price_scrape` NULL); der Watchdog misst nur „Task lief", nicht „Task hat Daten geschrieben".
2. **Der Watchdog war selbst tot** *(Fix in PR #7)*: `check_pipeline_health` scheiterte seit 30.06. **1121-mal in Folge** am Event-Loop-Bug — und konnte deshalb nie alerten.
3. **Klartext-Credentials ohne Funktion:** Die Settings-Seite nimmt eBay-Secret und Kleinanzeigen-Passwort entgegen und legt sie unverschlüsselt in `app_settings` ab — kein Code liest sie je.

## 1a. Server-Audit-Nachtrag (2026-08-16, read-only via SSH `lego-prod`)

- Alle 6 Container laufen (api/beat/frontend/worker „Up 6 weeks", postgres/redis „Up 4 months"); Server-Git auf `5e63e9b` (= Stand vor PR #7).
- **Backups funktionieren:** Cron `30 3 * * *` ist installiert — mit dem **korrekten** Pfad `/srv/lego-arbitrage` (nur das Doku-Beispiel nennt `/opt`); tägliche Dumps, neuester von heute 03:30 (~8 KB, die DB ist fast leer). Offen bleiben Restore-Probe und Off-site (`BACKUP_REMOTE`).
- **Neuer Befund — Event-Loop-Bug im Worker:** Gepoolte asyncpg-Verbindungen wanderten über `asyncio.run`-Loop-Grenzen → „got Future attached to a different loop". `check_pipeline_health` 1121 Fails in Folge (nie alertet, `last_alerted_at` leer), `refresh_auction_watchlist` 48 Fails, Metadaten-Refresh flatterte. Fix: gemeinsamer `run_async` mit `engine.dispose()` pro Task-Lauf ([async_runner.py](backend/app/tasks/async_runner.py), PR #7).
- **DB-Zahlen:** offers **0**, price_records **0**, aktive Watchlist **0**, lego_sets 5, inventory 5. Die Inventar-Bewertung arbeitet (`updated: 5, sell_signals: 5`); Catawiki-Discovery hat 2 Plattformen konfiguriert, findet aber 0.
- Die Heartbeat-Historie bestätigt den Scrape-Befund: `scrape_all_watched_sets` meldet „success" mit `total_sets: 0`.

## 2. Zielbild

Autonomes Arbitrage-System für **neue/OVP Lego-Sets** (Nutzerangabe 2026-08-16; deckungsgleich mit [LEGO-Arbitrage-Masterplan.html](LEGO-Arbitrage-Masterplan.html), 2026-03-20): Das System beobachtet Kleinanzeigen, Catawiki, eBay und Amazon, erkennt Angebote unter Marktwert, bewertet sie gegen einen Multi-Quellen-Konsens (ROI, Risiko, GO/NO-GO) und alertet via Telegram. Zweites Standbein: Inventarisierung und laufende Bewertung der eigenen Sammlung inkl. Verkaufsempfehlung. Ausbaustufe: möglichst automatisierter Verkauf über eBay und Kleinanzeigen. Der Masterplan sieht 5 Phasen vor; fertig ist das System, wenn Discovery→Bewertung→Alert ohne Zutun läuft und Verkauf mit minimalem Handgriff möglich ist. **Ist-Zuordnung:** Phase 1/2/4 im Wesentlichen gebaut, Phase 5 teilweise (Heartbeat, Backup-Skripte), Phase 3 (ML/AI) nicht begonnen.

## 3. Ist-Stand-Ledger

`WIRKSAM*` = Code verifiziert + Stack lief nachweislich am 2026-08-11 (6 Container, Beobachtung aus fremder Session); Feature-Ebene auf dem Server nicht einzeln geprüft.

| Baustein | Behauptung (Quelle + Datum) | Realität (Beleg + Datum) | Status | Konsequenz |
|---|---|---|---|---|
| Prod-Deploy-Pipeline (CI verify + SSH-Deploy) | [deploy.md](docs/deploy.md) 2026-06-29 | [deploy-production.yml](.github/workflows/deploy-production.yml): pytest, ruff, alembic check, Compose-/Caddy-Validierung, Deploy nach `/srv/lego-arbitrage`; Container liefen 11.08.; Auto-Migration (`alembic upgrade head` zwischen Build und Container-Tausch) seit PR #13+#14 (19.08.) — der erste automatische Lauf brach kontrolliert ab (fehlendes `prepend_sys_path`, Fix in #14), der Fail-Safe hielt Prod auf altem Stand | WIRKSAM* | Deploy-Weg für den Fix ist vorhanden und scharf |
| Deal-Checker (Live-Analyse 6 Quellen, Konvolut, EAN/Barcode, Max-Gebot, History) | Project_knowledge 2026-03-26 | [deal_analysis.py:102-113](backend/app/services/deal_analysis.py), [analysis.py](backend/app/api/routes/analysis.py) (1059 LOC); unabhängig vom Beat-Bug | WIRKSAM* | Kern-Nutzwert existiert — auf Abruf, nicht autonom |
| Inventar inkl. Fotos, Bewertungs-Snapshots, Sell-Links | Project_knowledge listet Fotos/EAN als „offen" (2026-03-26) | implementiert: [inventory.py](backend/app/api/routes/inventory.py) (714 LOC), [inventory_photo.py](backend/app/models/inventory_photo.py) | WIRKSAM* | Doku ist 3 Monate hinter dem Code |
| Telegram-Kanal (5 Alert-Typen, Test-Endpoint) | deploy.md 2026-06-29 | [telegram_bot.py](backend/app/notifications/telegram_bot.py) (269 LOC) | WIRKSAM* | Kanal ok — Deal-Alerts feuern mangels Daten trotzdem nie |
| Heartbeat/Pipeline-Health (`task_heartbeats`, `/api/system/status`) | PR #5 „P0 trust & safety" 2026-06-29 | Heartbeat-Schreiben funktioniert; der Watchdog-Task selbst scheiterte aber seit 30.06. am Event-Loop-Bug (1121×, nie alertet — Server-Audit 2026-08-16); Fix in PR #7. Bleibt konzeptionell blind für geschluckte Fehler (misst Task-Erfolg, nicht Nutzarbeit) | WIDERSPRUCH | Nach PR #7 wieder aktiv; Nutzarbeits-Metrik nachrüsten |
| Auth + Settings-Page | Design-Doc erklärte beides zum Non-Goal (2026-03-22) | existieren seit 2026-03-24 ([auth.py](backend/app/api/routes/auth.py), [Settings.jsx](frontend/src/pages/Settings.jsx)) | WIRKSAM* | stillschweigende Plan-Abweichung, ok |
| **Scheduled Scraping** (`scrape-all-sets`, alle 6 h) | „Watchlist-Sets werden regelmäßig gescraped" (Project_knowledge:34, 2026-03-26) | `datetime.now(datetime.UTC)` → AttributeError in [scrape_daily.py:85](backend/app/tasks/scrape_daily.py:85) (seit Commit 5c5f919, 2026-03-25), pro Set geschluckt (:230-232), Task meldet Erfolg; einzige Schreibstellen für `offers`/`price_records` (:95, :165) unerreichbar | **WIDERSPRUCH** | **Kern der Autonomie seit ~5 Monaten außer Betrieb; Fix in PR #7 (66bfd5d) — zusätzlich Watchlist befüllen, sie ist leer** |
| Live-Feed, `analyze-new-offers`, Tages-Summary | Beat-Schedule ([celery_app.py:45-61](backend/app/tasks/celery_app.py)) | Feed liest nur `offers`-Cache ([scout.py:79-108](backend/app/api/routes/scout.py)) → dauerhaft leer; Summary returned hart `"sent": True` ([analyze_new.py:150](backend/app/tasks/analyze_new.py)), „Bester Deal" nie gerendert (`best_analysis = None`, :140) | **WIDERSPRUCH** | Folgekette des Scrape-Bugs + eigene Ehrlichkeitslücken |
| Status-Anzeigen & Runbook-Checks | Design-Doc: „Scraper health, last scan, countdown" | [SystemStatus.jsx:6](frontend/src/components/SystemStatus.jsx) fetcht hartkodiert `/health` (in Prod falsche URL — `/lego`-Präfix fehlt); `/health` ist konstant „healthy" ohne DB/Redis-Check ([main.py:90-97](backend/app/main.py)); `/api/system/status` wird vom Frontend nie aufgerufen; Runbook-curl ohne Cookie → 401 ([deploy.md:142-145](docs/deploy.md)) | **WIDERSPRUCH** | Grüne Anzeige sagt nichts über die Pipeline aus |
| „Automated DB backups" | Commit 8436673 (2026-06-29) | Server-verifiziert 2026-08-16: Cron installiert, korrekter `/srv`-Pfad, tägliche Dumps (neuester 16.08. 03:30); nur das Doku-Beispiel nennt `/opt` ([deploy.md:173](docs/deploy.md:173)); Restore-Verify + Off-site weiter offen | WIRKSAM | Doku-Pfad fixen; einmal Restore-Probe in Wegwerf-DB |
| `.env.example`-Produktionsfalle | Runbook: „Copy .env.example to .env" (deploy.md:29) | Example setzt `AUTO_CREATE_TABLES_ON_STARTUP=true` (umgeht Alembic) und `SESSION_COOKIE_SECURE=false` ([.env.example:44-45](backend/.env.example)) | WIDERSPRUCH | Wer dem Runbook folgt, verliert Migrationsstand + Secure-Cookie |
| Auction-Discovery Catawiki/Whatnot/BrickLink (PR #4) | „Scan configured categories once per day" | Code gemergt ([catawiki.py](backend/app/services/catawiki.py) u. a.); `*_scan_urls` ohne Default → Scan läuft ohne Server-Konfig leer und meldet „ok"; BrickLink-Festpreise landen als „current_bid" im Alert | UNKLAR | Wirksamkeit hängt an `app_settings` auf dem Server — prüfen |
| Verkaufs-Automatisierung eBay/Kleinanzeigen | Vision (Nutzer); Settings-Felder existieren | Nur Deep-Link + Copy-Paste-Text ([inventory.py:311-344](backend/app/api/routes/inventory.py)); einziger ausgehender POST im Backend ist Telegram; Credential-Felder werden nie gelesen, aber im Klartext gespeichert | OFFEN-GATE | Grundsatzentscheidung nötig (s. Abschnitt 7); Felder solange entfernen |
| Phase 3: ML-Retraining + AI-Agent | Masterplan Phase 3; `weekly-retrain` im Beat-Schedule | [app/ml/](backend/app/ml/__init__.py) und [app/agent/](backend/app/agent/__init__.py) sind 0 Bytes; Task ist Placeholder ([analyze_new.py:153-167](backend/app/tasks/analyze_new.py)); kein `anthropic`/`sklearn`-Import | OFFEN-PFAD | Bewusst geparkt bis Datensammlung steht; Beat-Eintrag täuscht Aktivität vor |
| Proxy-/Captcha-Strategie (Kleinanzeigen, Amazon) | Eigene Doku verlangt Playwright+Stealth ([kleinanzeigen.py:36-37](backend/app/scrapers/kleinanzeigen.py)) | Kein `playwright`-Import; alles plain httpx ([base.py:122-135](backend/app/scrapers/base.py)); `USE_STEALTH_MODE`, `CACHE_TTL_SECONDS` u. a. sind tote Schalter; Chromium wird trotzdem in jedes Image installiert | OFFEN-PFAD | Robustheit der wichtigsten Quellen ungelöst |
| Server-Realität (Container, Heartbeats, Backups, Selektoren) | deploy.md/infrastructure.md | von hier nicht prüfbar; einziger Live-Beleg: Deployment-Liste einer fremden Session, 2026-08-11, `lego-arbitrage running(6)` | OFFEN-GATE | SSH-Audit als nächster Verifikationsschritt |
| Branch `archive/auction-watch-wip-2026-04-21` | „WIP archive" (4 Commits, 2026-04-21) | nie gemergt; 2 Tage später als PR #4 (Codex) neu gebaut; verwaiste `.pyc` (`auction_calculator`, `test_auction_calculator`, `test_analysis_multi`) passen dazu | VERWAIST | löschen (Inhalt ersetzt) oder bewusst behalten |
| Branch `master` | Init-Artefakt 2026-03-20 | disjunkte Root, 1 Commit, kein merge-base zu main | VERWAIST | lokal + remote löschen |
| Tote Config/Deps | .env.example, pyproject | SMTP-Block ohne Sendepfad (kein `aiosmtplib`-Import), `anthropic` ungenutzt, leere Dirs (`.agents/`, `kleinanzeigen-uploads/`, `infra/grafana/`), [frontend/README.md](frontend/README.md) = Vite-Template, kein Root-README | VERWAIST | aufräumen oder implementieren |
| Lokales main | — | 4 Commits behind origin/main (Merge PR #5/#6 nie gepullt) | VERWAIST | `git checkout main && git pull --ff-only` |

## 4. Kritischer Pfad

1. **Scrape-Bug fixen** — [scrape_daily.py:85](backend/app/tasks/scrape_daily.py:85): `from datetime import UTC, datetime`; `datetime.now(UTC)`. Dazu ein Test, der `_scrape_set_prices_async` mindestens bis hinter Zeile 85 treibt (schlägt vor dem Fix fehl). *Verifikation:* pytest lokal grün, neuer Test rot→grün. Quick Win, < 30 min.
2. **Deploy** `GATE` — Push auf main triggert Prod-Deploy. *Verifikation:* Actions grün; danach auf dem Server `SELECT count(*) FROM offers/price_records` wachsend; Live-Feed füllt sich binnen 6 h.
3. **Server-Audit (read-only)** `GATE` (SSH-Zugang) — `docker compose ps`, `crontab -l` (Backup-Cron? richtiger Pfad `/srv`?), neuestes Dump-Datum unter `${DATA_ROOT}/backups`, `SELECT * FROM task_heartbeats`, Stichprobe ob Scraper-Selektoren gegen die Live-Seiten noch greifen. *Verifikation:* Werte dokumentiert, STATUS aktualisiert.
4. **Watchdog ehrlich machen** — Heartbeat misst Nutzarbeit (z. B. `items_scraped > 0` für Scrape-Tasks), `daily_summary` meldet `sent` nur bei echtem Versand, [SystemStatus.jsx](frontend/src/components/SystemStatus.jsx) nutzt `HEALTH_URL` + `/api/system/status`, `/health` prüft DB. *Verifikation:* künstlich kaputter Task erzeugt binnen 1 h einen Telegram-Alert.
5. **Backup-Kette schließen** `GATE` (Serverkonfig) — Cron korrekt auf `/srv/lego-arbitrage`, Doku-Beispiel fixen ([deploy.md:173](docs/deploy.md:173), [backup-db.sh:11](scripts/backup-db.sh:11)), `BACKUP_REMOTE` setzen (Off-site), einen Restore in Wegwerf-DB durchführen und dokumentieren. *Verifikation:* frischer Dump < 24 h alt + dokumentierter Restore.
6. **Discovery konfigurieren oder abschalten** — `catawiki_scan_urls` & Co. in `app_settings` setzen, sonst liefert der Tagesscan dauerhaft „0 discovered/ok"; BrickLink-Preis nicht als „Gebot" labeln. *Verifikation:* Discovery-Summary > 0 oder Job bewusst deaktiviert.
7. **Entscheidung Verkaufsweg** `GATE` (Nutzer-Entscheidung, Abschnitt 7) — danach Implementierung; bis dahin ungenutzte Credential-Felder aus Settings entfernen.
8. **Doku nachziehen** — Project_knowledge.md aktualisieren (Stand März!), Root-README, `.env.example` entschärfen (`AUTO_CREATE_TABLES_ON_STARTUP=false`, `SESSION_COOKIE_SECURE=true`, `HEARTBEAT_*`-Keys ergänzen), Runbook-curls korrigieren.
9. **Phase 3 (ML/AI)** — erst sinnvoll, wenn 1–6 stehen und Monate an Preisdaten gesammelt sind; das Modell braucht genau die Daten, die seit März nicht geschrieben werden.

**Quick Wins außerhalb des Pfads:** lokales main ff-pullen; Branches `master` + `archive/auction-watch-wip` löschen; verwaiste `.pyc` löschen; leere Verzeichnisse entfernen.

## 5. Parkplatz & Verwaistes

**Ignorieren erlaubt (PARK):** Idealo-Scraper (nur Preis, bewusst schlank); Whatnot/BrickLink `get_price=None` (Design); leeres `infra/grafana/`; Umzug in Company-GitHub-Org („optional", [infrastructure.md:53-55](docs/infrastructure.md)); TimescaleDB/LangGraph/Next.js aus dem Masterplan (durch einfachere Realität ersetzt).

**Schließen/Löschen (VERWAIST):** siehe Ledger — beide Alt-Branches, verwaiste `.pyc`, leere Verzeichnisse, SMTP-/Anthropic-Config ohne Code, ungenutzte Settings-Credential-Felder, Template-README.

## 6. Realitäts-Check-Funde (adversarialer Gegenblick)

Der Gegenblick-Scan fand 15 Behauptung-vs-Realität-Lücken; die wesentlichen sind oben ins Ledger eingearbeitet. Sie fielen im ersten Durchgang durch, weil die Inventur-Scouts Artefakte und Behauptungen *katalogisiert*, aber nicht gegen den Code *ausgeführt/verfolgt* haben. Die wichtigsten, vom Hauptagenten am Code nachverifiziert:

1. **Scrape-Pipeline tot seit 2026-03-25** ([scrape_daily.py:85](backend/app/tasks/scrape_daily.py:85)) — selbst verifiziert: einzige `datetime.UTC`-Stelle, liegt vor jedem Scraper-Aufruf in der Funktion, die als einzige `offers`/`price_records` schreibt (:95/:165, nächste Funktion erst :210). Fehler wird pro Set geschluckt, Heartbeat meldet „ok".
2. **`"sent": True` ohne Versand** + nie gerenderter „Bester Deal" ([analyze_new.py:140-150](backend/app/tasks/analyze_new.py)).
3. **Status-UI misst das Falsche** (hartkodiertes `/health` ohne `/lego`-Präfix; konstantes „healthy"; `/api/system/status` frontend-seitig unbenutzt).
4. **Settings sammeln Klartext-Credentials für nicht existierende Funktionen** ([settings.py:37-63](backend/app/api/routes/settings.py)).
5. **BrickLink-Festpreise als Auktionsgebote**; Discovery ohne konfigurierte URLs dauerhaft leer-grün ([catawiki_scan.py:29-31](backend/app/tasks/catawiki_scan.py)).
6. **Bestätigt wurde:** Deal-Checker echt, CI scharf, 9 Testdateien ohne Skips, Schema/Migrationen konsistent, Telegram implementiert.

## 7. Entscheidungen & Verworfenes / Offene Entscheidungen

**Entschieden (mit Beleg):**
- Privates GitHub-Repo `BastiFasti0811/Lego-Arbitrage` ist canonical (Commit 4f7e731, 2026-03-25).
- Hosting: Mitnutzung des SmartPrepMeal-Servers `spm-prod-01` (Hetzner, 178.104.97.121), shared Host-Caddy, Pfad-Präfix `/lego`, App-Dir `/srv/lego-arbitrage` (2026-03-25).
- Frontend-Stack abweichend vom Design-Doc: fetch + react-query statt Axios, kein Framer Motion (faktisch 2026-03-23).
- Auth + Settings-Page trotz YAGNI-Beschluss gebaut (2026-03-24).
- Auction-Watch-Erstversuch verworfen und als Archiv-Branch eingefroren; Neuansatz via Codex → PR #4 (2026-04-21 → 2026-05-04).
- Benachrichtigung via Telegram; E-Mail nie gebaut (faktisch, seit März).
- „P0 Trust & Safety" (Heartbeat + Backups) vor neuen Features (2026-06-29).

**Offen zu entscheiden (blockiert Folgeschritte):**
1. **Verkaufsweg:** eBay bietet eine offizielle Sell-API (machbar, Aufwand: OAuth + Listing-Flow). Kleinanzeigen hat **keine** offizielle API — Automatisierung hieße Browser-Automation gegen die ToS mit Konto-Risiko. Optionen: (a) nur eBay automatisieren, Kleinanzeigen bleibt Copy-Paste; (b) beides manuell lassen; (c) Kleinanzeigen-Automation bewusst riskieren.
2. **Proxy/Captcha:** Playwright wirklich einbauen (steckt schon im Image) vs. Scraping-Frequenz niedrig halten und mit httpx leben.
3. **Off-site-Backup:** `BACKUP_REMOTE` (rclone-Ziel) festlegen.
4. **Phase-3-Scope:** XGBoost-Retraining + Claude-Agent aus dem Masterplan halten oder auf die simple Feedback-Kalibrierung ([deal_analysis.py:174-189](backend/app/services/deal_analysis.py)) reduzieren.

**Doku-Lücke:** Der reale Server-Zustand (Cron ja/nein, Backup-Historie, Heartbeat-Inhalte) existiert in keinem Artefakt — nur auf dem Server selbst.

## 8. Quellenkarte

| Quelle | Ort | Stand | Verlässlichkeit |
|---|---|---|---|
| Git/Branches/PRs | Repo + GitHub `BastiFasti0811/Lego-Arbitrage` | erhoben 2026-08-16 | hoch |
| Code-Realität | `backend/` (10.296 LOC), `frontend/src` (3.967 LOC) | HEAD e4606d5, 2026-06-30 | hoch (Schlüsselstellen doppelt verifiziert) |
| [Project_knowledge.md](Project_knowledge.md) | Repo-Root | **2026-03-26 — 3 Monate stale** (nennt Implementiertes „offen") | niedrig |
| [docs/deploy.md](docs/deploy.md) | Repo | 2026-06-29 | mittel (Runbook-Kommandos teils falsch, Pfad-Widerspruch `/opt` vs `/srv`) |
| [docs/infrastructure.md](docs/infrastructure.md) | Repo | 2026-03-25 | mittel (Health-URL falsch) |
| [Masterplan](LEGO-Arbitrage-Masterplan.html) + [docs/plans/](docs/plans/) | Repo | 2026-03-20/22 | Plan-Dokumente, kein Ist-Stand |
| Lokale Laufzeit | Windows-Maschine BASTIFASTILAPPI | geprüft 2026-08-16: nichts läuft, keine `.env`, keine DB; letzter Testlauf 2026-06-29 abends | hoch |
| Server-Laufzeit | `spm-prod-01` (178.104.97.121), `/srv/lego-arbitrage` | SSH-Audit 2026-08-16 (read-only): Container, Cron, Backups, Heartbeats, DB-Counts geprüft — siehe Abschnitt 1a | hoch |
| Agent-Memory / Pläne / frühere Sessions | `~/.claude/...` | leer bzw. ohne Lego-Bezug (geprüft 2026-08-16); Entwicklung lief außerhalb der CCD-Sessions (vermutlich Codex, vgl. `codex/*`-Branches) | — |
| Ticket-System | — | keines vorhanden (Privatprojekt) | — |
