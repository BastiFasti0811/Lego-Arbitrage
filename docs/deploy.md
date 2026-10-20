# Deployment Runbook

## Goal

Run production from GitHub on **victus** (Sebastian's Linux machine at home,
NixOS) under `/srv/lego-arbitrage` and keep that checkout a runtime target
instead of an editing machine. The source-of-truth repo intentionally remains
the private GitHub repo `BastiFasti0811/Lego-Arbitrage`.

Until early October 2026 production ran on the Hetzner host `spm-prod-01`
(`178.104.97.121`). That host no longer answers; nothing in this repo should
point at it any more.

## Recommended Topology

- GitHub remains the source of truth for code, history and rollback.
- The checkout `/srv/lego-arbitrage` on victus runs the containers and holds
  the persistent data only; code is never edited there.
- Production uses [docker-compose.prod.yml](../docker-compose.prod.yml).
- victus already runs a shared Caddy container (`smartprepmeal-caddy-1`,
  config `/opt/SmartPrepMeal/Caddyfile`) on ports 80/443.
- The LEGO routes from [infra/Caddyfile](../infra/Caddyfile) are merged into
  that host-level Caddy config instead of starting a second port-80/443 proxy.
- App URL: `http://localhost/lego` on victus, `http://<victus LAN IP>/lego`
  from the home network (on 20.10.2026 `192.168.50.89`, DHCP, not fixed).
- Local development can continue to use the existing `docker-compose.yml`
  with the local `nginx` proxy.

## First-Time Server Setup

1. Clone the repository into a stable app directory, for example
   `/srv/lego-arbitrage`, as the user that runs the deploys (on victus:
   `sebastian`).
2. Copy [.env.prod.example](../.env.prod.example) to `.env.prod`.
3. Copy [backend/.env.example](../backend/.env.example) to `backend/.env`.
4. Replace every placeholder secret before the first start.
5. Create the persistent data directories below `DATA_ROOT`. On victus
   `DATA_ROOT` is still `/mnt/HC_Volume_105179687/lego-arbitrage`: the name
   comes from the Hetzner volume, the directory now lives on victus' local disk.

```bash
mkdir -p /mnt/HC_Volume_105179687/lego-arbitrage/postgres
mkdir -p /mnt/HC_Volume_105179687/lego-arbitrage/redis
mkdir -p /mnt/HC_Volume_105179687/lego-arbitrage/media
```

## GitHub Access From The Prod Checkout

The checkout pulls over SSH. On victus it uses the existing `github-personal`
alias from `~/.ssh/config` (key of the private account `BastiFasti0811`);
plain `git@github.com:` fails there with `Permission denied (publickey)`.

```bash
git -C /srv/lego-arbitrage remote set-url origin git@github-personal:BastiFasti0811/Lego-Arbitrage.git
git -C /srv/lego-arbitrage fetch origin
```

A read-only repository deploy key is the cleaner option for a dedicated
server; add it under its own `Host` alias and point `origin` at that alias.

## Continuous Integration

[ci.yml](../.github/workflows/ci.yml) runs the `verify` job (deploy-script
test, frontend lint and build, pytest, ruff, `alembic upgrade head` and
`alembic check` against an empty PostgreSQL, Compose and Caddy validation) on
pull requests against `main`, on pushes to `main` and on manual dispatch. PR
runs use their own concurrency group, so a new push cancels the stale run.

CI does **not** deploy. GitHub-hosted runners cannot reach victus, and the
former SSH deploy job (secrets `PROD_SSH_KEY`, `PROD_KNOWN_HOSTS`, variables
`PROD_HOST`, `PROD_USER`, `PROD_APP_DIR`, `PROD_URL`, environment
`production`) targeted the retired Hetzner host. Those secrets and variables
are unused now and can be deleted in the repository settings.

If the deploy should run without a human again, the options are a
self-hosted GitHub runner on victus that runs `bash scripts/deploy-prod.sh`,
or SSH from the runner to victus over a private network such as Tailscale.

## Required Config Split

- `.env.prod`: host-level Compose values such as `DATA_ROOT`,
  `POSTGRES_PASSWORD`
- `backend/.env`: application secrets and runtime settings such as dashboard
  auth, Telegram token defaults, AI keys and scraper config; include `ANTHROPIC_API_KEY`
  for the photo-analysis/listing-text AI (PR 2)—without it, AI endpoints return 503 and
  everything else keeps working
- Inventory-Fotos liegen unter `MEDIA_ROOT`, in Produktion per Compose auf
  `${DATA_ROOT}/media` gemountet
- `backend/.env` in Produktion zusaetzlich: `SESSION_COOKIE_PATH=/lego`. Die App
  laeuft dort unter `/lego` auf demselben Host und hinter demselben Caddy wie
  andere Apps; ohne den Pfad gilt das Session-Cookie fuer den ganzen Host.
  Der Browser prueft den Pfad gegen `/lego/api/...`, dass Caddy das Praefix vor
  dem Backend abschneidet, aendert daran nichts. Lokal bleibt der Default `/`.
- Dashboard-Sessions liegen in Redis (Key `lego:session:*`, Laufzeit 30 Tage),
  der Login-Zaehler ebenfalls (`lego:login_attempts:<ip>`, bei IPv6 pro /64; 5 Fehlversuche pro
  15 Minuten, danach HTTP 429). Ist Redis weg, ist niemand angemeldet (401)
  und der Login antwortet mit 503.

## Deploy

After a merge to `main` with a green `verify` run, deploy on victus:

```bash
cd /srv/lego-arbitrage
git branch --show-current          # must print: main
bash scripts/deploy-prod.sh        # pulls main, builds, migrates, swaps, health-checks
git fetch origin && test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" && echo "prod = origin/main"
curl -fsS http://localhost/lego/health
```

The script is the only supported way to deploy. It aborts before any git or
docker call when the checkout is not on `main` (override with
`DEPLOY_BRANCH`), because `git pull --ff-only` on another branch would deploy
that branch. On success it prints the deployed commit.

The script first takes a non-blocking lock on
`/tmp/lego-arbitrage-deploy.lock` (path overridable via `LOCK_FILE`), so a
manual run cannot interleave with a CI deploy — the second run aborts
immediately. Always deploy as the same user: a lock file once created via
`sudo` makes later deploys fail with a permission error until root removes
it. The script then performs these steps in order:

1. `git pull --ff-only`
2. `docker compose ... build` — build the new images while the old containers
   keep serving
3. `docker compose ... run --rm -T api alembic upgrade head` — apply database
   migrations from the freshly built image in a one-off container
4. `docker compose ... up -d --remove-orphans` — swap the containers to the
   new images
5. Poll the API health endpoint for up to 60 seconds

A failed migration aborts the deploy before any app container is restarted:
the previous code keeps running against the previous schema (PostgreSQL DDL is
transactional, and `alembic/env.py` runs the whole upgrade in one transaction,
so a failed run rolls back completely). One caveat: `compose run` converges
the postgres/redis dependencies first, so a deploy that also changes their
compose config can recreate those two before the migration step. Because the
old containers keep serving while migrations run, **migrations must stay
backward compatible** with the previous release — additive changes only; use
the expand/contract pattern for renames and drops.

Fresh installations need no extra migration step: `compose run` starts
postgres as a dependency, waits for it to become healthy, and applies the full
schema before the API starts.

Existing installations that were created by the old startup `create_all`
bootstrap should be stamped once after confirming the schema matches the repo:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm api alembic stamp head
```

The script's call sequence and abort behavior are covered by
[tests/deploy-prod.test.sh](../tests/deploy-prod.test.sh) (stubbed `docker`
and `git`, no containers involved), which also runs in the workflow's verify
job:

```bash
bash tests/deploy-prod.test.sh
```

## Health Checks

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f --tail=200
curl -sS http://127.0.0.1/lego/health
```

### Pipeline Health

Beyond the API `/health` endpoint, the worker tracks when each scheduled task
last ran/succeeded. The `pipeline-health-check` beat job (hourly) sends a
Telegram alert when a task is **stale** (no recent success) or **failing**, so a
silently broken scraper surfaces instead of going unnoticed. Inspect it any time:

```bash
# Cookie-authenticated dashboard endpoint
curl -sS http://127.0.0.1/lego/api/system/status | jq
```

Tuning lives in `backend/.env`: `HEARTBEAT_ENABLED` (default `true`) and
`HEARTBEAT_REALERT_HOURS` (default `6`, throttles repeat alerts per task).

## Backup

The PostgreSQL data lives under `${DATA_ROOT}/postgres` on victus' local disk;
nothing backs it up automatically. Use the bundled scripts to take and verify
logical dumps, and set `BACKUP_REMOTE` for a copy that survives a disk loss.

```bash
# One-off backup (writes a verified, gzipped dump to ${BACKUP_DIR:-${DATA_ROOT}/backups})
bash scripts/backup-db.sh

# List backups
ls -lh /mnt/HC_Volume_105179687/lego-arbitrage/backups

# Restore a dump (DESTRUCTIVE — drops & recreates objects)
CONFIRM=yes bash scripts/restore-db.sh /mnt/HC_Volume_105179687/lego-arbitrage/backups/<file>.sql.gz
```

Schedule it nightly via cron on the server (as the deploy user). On victus
(NixOS) a user crontab or a systemd timer from the system configuration does
the same job; as of 20.10.2026 none is set up and the newest dump is from
30.06.2026.

```bash
crontab -e
# Daily 03:30 — adjust the repo path to your checkout. The explicit PATH is
# required because cron's default PATH does not include docker/rclone.
30 3 * * * cd /srv/lego-arbitrage && PATH=/usr/local/bin:/usr/bin:/bin bash scripts/backup-db.sh >> /var/log/lego-backup.log 2>&1
```

Backup tunables live in `.env.prod`: `BACKUP_DIR`, `BACKUP_KEEP` (retained
dumps, default 14) and `BACKUP_REMOTE` (optional rclone destination for a true
off-site copy — the local dump alone does not survive volume loss).

> **Verify restores.** A backup you have never restored is not a backup. After
> the first nightly run, restore the latest dump into a throwaway database and
> confirm row counts before trusting it.

## Rollback

```bash
git fetch --all --tags
git checkout <known-good-commit-or-tag>
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

The checkout is now on a detached HEAD, so `scripts/deploy-prod.sh` refuses
to run until it is back on `main`. Once the fix is merged:
`git checkout main && bash scripts/deploy-prod.sh`.

Rolling back the code does not downgrade the schema. As long as migrations
follow the backward-compatibility rule above, the previous release runs fine
against the newer schema; a schema downgrade (`alembic downgrade`) is a
deliberate manual step.

## Catawiki Home Scanner

Prod does not scan Catawiki itself (`HOME_RUNNER_PLATFORMS`); a machine with a
home internet connection fetches jobs from `/api/remote-scan/runner/job` and
posts results back (see `backend/app/tools/catawiki_home_scan.py`). Its
`LEGO_API_URL` must be the app URL including `/lego`:

- on victus itself: `http://localhost/lego`
- from another machine in the home network: `http://<victus LAN IP>/lego`
  (Windows: `scripts/install-catawiki-home-scan.ps1 -ApiUrl http://<victus LAN IP>/lego`)

The old address `http://178.104.97.121/lego` is dead. As of 20.10.2026 the
prod database holds no `remote_scan_token` and no `catawiki_scan_urls`, so no
scanner is connected yet.

## GitHub Account Note

- Private account: `BastiFasti0811`
- Work account: `conuti-sebastian-willkommen`
- Canonical repo today: `BastiFasti0811/Lego-Arbitrage`

For local development, use remotes that clearly target the intended GitHub
account so Git Credential Manager does not silently reuse the wrong identity.
For the server, prefer a deploy key or a dedicated machine credential instead of
interactive developer credentials.
