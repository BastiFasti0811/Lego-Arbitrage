# Einstieg Lego-Arbitrage

Eine Seite für neue Agenten und für Sebastian nach einer Pause. Sie verweist auf die Quellen, statt Stände abzuschreiben: Zahlen und Status hier nicht pflegen, sondern live abfragen.

## Was das System tut

Findet Lego-Deals (Kleinanzeigen, eBay, Catawiki), führt das eigene Inventar (Lego und andere Artikel) und begleitet den Verkauf über Kleinanzeigen und eBay. Begriffe: [CONTEXT.md](../CONTEXT.md). Ziel mit Zahlen und Frist (1.12.2026): Memory `projekt-ziel`.

## Prod

| | |
|---|---|
| Rechner | victus (Linux/NixOS, Heimnetz), Checkout `/srv/lego-arbitrage` auf `main` |
| Container | `lego-api-prod`, `lego-worker-prod`, `lego-beat-prod`, `lego-frontend-prod`, `lego-postgres-prod`, `lego-redis-prod` |
| Zugang | `http://localhost/lego` auf victus, `http://<LAN-IP>/lego` im Heimnetz, über den SmartPrepMeal-Caddy (`:80`). Kein HTTPS, siehe Ticket #7 |
| Hetzner | `178.104.97.121` / `spm-prod-01` ist abgeschaltet. Nichts soll mehr darauf zeigen |

## Deploy, Health, Rollback

- Deploy nur auf victus mit `bash scripts/deploy-prod.sh`; CI (`verify`) deployt nicht. Ablauf: [deploy.md#deploy](deploy.md#deploy).
- Health: `curl -fsS http://localhost/lego/health` muss als `version` den Commit von `git rev-parse HEAD` liefern, `checks` alle `ok`. Gesamtprüfung: [deploy.md#post-deploy-health-check](deploy.md#post-deploy-health-check).
- Rollback: [deploy.md#rollback](deploy.md#rollback) (`scripts/rollback-prod.sh`).
- Backup und Restore: [deploy.md#backup](deploy.md#backup).
- Wann Agenten selbst mergen und deployen dürfen: Memory `merge-und-deploy-regel` (nur nachts, nach Fremdmodell-Review mit PR-Kommentar).

## Wo die Zahlen stehen

```bash
docker exec lego-api-prod python -m app.tools.goal_progress
```

Liefert den Zielstand (inseriert/30, verkauft/15, Deals je Woche/3). Gezählt werden nur Lego-Artikel. Den Verlauf je Nacht führt die Memory-Notiz `laufprotokoll`.

## Wissen und Regeln

- **Projekt-Memory** (AgentsRoom): lokaler Spiegel unter `.agentsroom/memory/INDEX.md`, schreiben nur über `memory_save`. Zuerst lesen: `agent-workflow`, `autopilot-nachtbetrieb`, `merge-und-deploy-regel`, `status-docs-drift-fast`.
- **Backlog** (AgentsRoom): Tickets mit Befund, Schritten und Akzeptanzkriterien. Offene Arbeit steht dort, nicht in dieser Datei.
- **Entscheidungen:** [adr/](adr/). ADR 0002 mit den Nachträgen 26.10. (Kleinanzeigen per Browser) und 02.11. (eBay per Sell API).
- **Betrieb und Infrastruktur:** [deploy.md](deploy.md), [infrastructure.md](infrastructure.md), [ebay.md](ebay.md).
- **Skills im Repo:** `.claude/skills/lego-eingang` (Fotos → Inventar), `.claude/skills/kleinanzeigen-einstellen` (Einstell-Aufträge per Browser).
- **Pläne und Specs:** [plans/](plans/), [superpowers/](superpowers/). Historisch, nicht als Stand lesen.

## Archiv

Alte Statusberichte liegen in [archive/](archive/): `STATUS-2026-08-16.md` (Audit vom 16.08., Hetzner-Zeit) und `status-2026-09-05.md`. Beide sind überholt und widersprechen sich teilweise. Aussagen daraus vor der Weitergabe gegen Git-Historie, CI und den Server prüfen (Memory `status-docs-drift-fast`).

## Arbeitsregeln für Agenten

- Eigener Worktree je Ticket. Im geteilten Baum `/home/sebastian/projekte/privat/Lego-Arbitrage` wechseln andere Sitzungen den Branch: vor jedem Commit `git branch --show-current`.
- Kein `git reset --hard`, kein `git clean`, kein `git push --force`, kein blindes `git add -A` (CRLF).
- Keine Secrets in Repo, Tickets, Memory oder Logs. Zugangsdaten stehen nur in `/srv/lego-arbitrage/backend/.env` (0600).
- Nie automatisch kaufen oder bieten. Einstellen nur auf den in ADR 0002 freigegebenen Wegen.
