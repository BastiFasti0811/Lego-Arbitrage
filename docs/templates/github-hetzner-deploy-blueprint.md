# GitHub + Hetzner Deploy Blueprint

Diese Vorlage extrahiert den in diesem Repo genutzten GitHub->Hetzner-Deploy-Prozess so, dass du ihn neuen Projekten frueh mitgeben kannst.

## Der Ablauf in diesem Repo

1. Ein Push auf `main` oder ein manueller `workflow_dispatch` startet den GitHub-Workflow.
2. GitHub Actions prueft vor dem Deploy Build, Lint, Compose-Konfiguration und Caddy-Konfiguration.
3. Der Deploy-Job verbindet sich per SSH mit dem Hetzner-Server.
4. Auf dem Server wird im App-Verzeichnis `git pull --ff-only` ausgefuehrt.
5. Danach startet `scripts/deploy-prod.sh` den produktiven Compose-Stack mit `docker compose up -d --build --remove-orphans`.
6. Ein Healthcheck prueft, ob die Anwendung nach dem Start erreichbar ist.
7. Caddy auf dem Host leitet Requests an Frontend- und API-Container weiter.

Hinweis: In diesem Repo passiert `git pull --ff-only` aktuell sowohl im Workflow-Aufruf als auch im Script. Fuer neue Projekte reicht eine zentrale Stelle. Die Vorlage in diesem Ordner legt den Pull nur in `scripts/deploy-prod.sh` ab.

## Was du neuen Projekten initial mitgeben solltest

- eine produktive Compose-Datei wie `docker-compose.prod.yml`
- eine serverseitige Deploy-Helper-Datei wie `scripts/deploy-prod.sh`
- eine GitHub-Action fuer Verify + SSH-Deploy
- eine `.env.prod.example` fuer hostnahe Werte
- eine App-`.env.example` fuer Anwendungskonfiguration
- eine Reverse-Proxy-Konfiguration fuer Caddy oder Nginx
- eine kurze Runbook-Datei mit Setup, Deploy, Healthcheck und Rollback

## Minimaler Standard fuer neue Projekte

### 1. Server vorbereiten

1. Auf dem Hetzner-Server einen normalen Deploy-User nutzen, zum Beispiel `deploy`.
2. Docker Engine und Docker Compose Plugin installieren.
3. Ein stabiles App-Verzeichnis anlegen, zum Beispiel `/srv/my-app`.
4. Repository auf dem Server klonen.
5. `.env.prod` und App-`.env` aus den Example-Dateien erzeugen.
6. Persistente Datenverzeichnisse ausserhalb des Repos anlegen.
7. Reverse Proxy auf dem Host vorbereiten.

### 2. GitHub sauber als Source of Truth nutzen

1. Code lebt in GitHub, nicht dauerhaft auf dem Server.
2. Der Server zieht nur den aktuellen Stand per `git pull --ff-only`.
3. Fuer den Server einen read-only Deploy Key nutzen, nicht den persoenlichen GitHub-Login.
4. Fuer GitHub Actions einen getrennten SSH-Key nur fuer den Serverzugriff nutzen.

### 3. GitHub Secrets und Variables anlegen

Pflicht:

- `PROD_SSH_KEY`: privater SSH-Key fuer GitHub Actions -> Server
- `PROD_KNOWN_HOSTS`: Ausgabe von `ssh-keyscan -H <server-ip>`

Optional:

- `PROD_HOST`: Server-IP oder DNS-Name
- `PROD_USER`: Deploy-User
- `PROD_APP_DIR`: Zielverzeichnis auf dem Server
- `PROD_URL`: Ziel-URL fuer das GitHub-Environment

Empfehlung:

- GitHub Environment `production` anlegen
- Deploy-Secrets auf dieses Environment begrenzen
- optional Reviewer als Freigabe fuer Live-Deploys setzen

## Kopierbare Startdateien

Die beiden Beispiel-Dateien in diesem Ordner kannst du in neue Projekte uebernehmen:

- [deploy-production.yml.example](./deploy-production.yml.example)
- [deploy-prod.sh.example](./deploy-prod.sh.example)

## Was pro Projekt angepasst werden muss

- Server-IP, User und App-Pfad
- Name der Compose-Datei
- benoetigte Services im `docker compose up`
- Healthcheck-Befehl
- benoetigte Env-Dateien
- Caddy- oder Nginx-Routen
- Volumes und Persistenzpfade

## LEGO-spezifische Werte in diesem Repo

Diese Werte sind hier bewusst projektspezifisch und sollten nicht blind uebernommen werden:

- Host: `178.104.97.121`
- User: `deploy`
- App-Verzeichnis: `/srv/lego-arbitrage`
- Route-Prefix in Caddy: `/lego`
- Externes Caddy-Netzwerk: `smartprepmeal_default`
- Persistenzpfad: `/mnt/HC_Volume_105179687/lego-arbitrage`
- Compose-Services: `postgres redis api worker beat frontend`
- Healthcheck: `docker exec lego-api-prod curl -fsS http://127.0.0.1:8000/health`

## Empfohlene Ordnerstruktur fuer neue Projekte

```text
.
|-- .github/
|   `-- workflows/
|       `-- deploy-production.yml
|-- docs/
|   `-- deploy.md
|-- infra/
|   `-- Caddyfile
|-- scripts/
|   `-- deploy-prod.sh
|-- .env.prod.example
`-- docker-compose.prod.yml
```

## Rollback-Muster

```bash
git fetch --all --tags
git checkout <known-good-commit-or-tag>
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

## Gute Defaults

- `git pull --ff-only` statt Merge auf dem Server
- SSH Host Key pinnen
- Healthcheck nach jedem Deploy
- `--remove-orphans` fuer Compose nutzen
- Secrets nur in `.env` oder GitHub Secrets halten, nie im Repo
- Root nur fuer Admin-Notfaelle verwenden
