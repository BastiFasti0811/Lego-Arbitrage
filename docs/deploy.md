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
   Keep `AUTO_CREATE_TABLES_ON_STARTUP=false` (Alembic owns the schema). As
   long as prod is served over plain HTTP (victus as of 20.10.2026), set
   `SESSION_COOKIE_SECURE=false`, otherwise the browser drops the session
   cookie and login fails.
4. Replace every placeholder secret before the first start, then
   `chmod 600 .env.prod backend/.env` (see [Secrets](#secrets)).
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

[ci.yml](../.github/workflows/ci.yml) runs the `verify` job (tests of the deploy,
uptime, health-check and rollback scripts, frontend lint and build, pytest, ruff, `alembic upgrade head` and
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

## Secrets

`.env.prod` and `backend/.env` hold every prod secret (database password,
session secret, API keys, Telegram token, Catawiki cookie). Three rules keep
them where they belong:

- **Mode 0600.** compose reads both files as the deploying user, nobody else
  needs them. `scripts/deploy-prod.sh` runs `chmod go-rwx` on both (and on any
  `.env.bak*` it finds) before every build, so a fresh copy cannot stay 0644.
- **Never in an image.** `backend/.dockerignore` keeps `.env`, `.env.*`
  (except `.env.example`), `tests/` and `data/` out of the backend build
  context; the containers get their settings from compose `env_file`. The root
  `.dockerignore` does the same for the frontend context `.`. Check after a
  build: `docker exec lego-api-prod sh -c 'ls -a /app'` lists no `.env*`
  besides `.env.example`.
- **Never in git, never in the repo directory.** The repository is public.
  `.gitignore` ignores `.env.*` except `*.example`. Backups of an env file go
  outside the checkout, e.g. `~/.local/share/lego-arbitrage/env-backups/`
  (directory 0700, files 0600); the deploy script warns when it finds one in
  the checkout.

Images built before 22.10.2026 and the BuildKit cache still contain the env
files of their time. Do not `docker builder prune` while the backend has no
lock file (the pip layer pins today's versions); rotate the secrets instead if
an image or `/var/lib/docker` backup ever left the host.

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
- Der Cookie-Pfad ist keine Sicherheitsgrenze gegen die Nachbar-Apps. Schreibende
  Aufrufe (POST/PUT/PATCH/DELETE unter `/api/*`, ausser `/api/remote-scan/runner/*`)
  lehnt die API mit 403 ab, wenn der Browser `Sec-Fetch-Site: same-site`/`cross-site`
  meldet oder ein `Origin` schickt, der weder zum `Host` passt noch in
  `CORS_ALLOWED_ORIGINS` steht (`backend/app/security/origin_guard.py`). Das
  schliesst Formular-POSTs von anderen Ports desselben Hosts. Skript-Code einer
  Nachbar-App unter `http://<host>/...` bleibt fuer den Browser same-origin; dagegen
  hilft erst ein eigener Origin fuer Lego (offen, zusammen mit HTTPS).
- `CORS_ALLOWED_ORIGINS` bleibt in Produktion leer (Frontend und API teilen sich
  den Origin). Ein Eintrag oeffnet CORS mit Cookie und schreibende Aufrufe fuer
  genau diesen Origin.
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
# unattended (autopilot): roll back by itself when the checks fail
bash scripts/deploy-prod.sh --auto-rollback
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
5. Poll the API health endpoint for up to 60 seconds until it answers 200
   (PostgreSQL and Redis reachable) **and** reports the deployed commit as
   `version`; otherwise the script prints the API log and fails
6. Run the full post-deploy check
   [scripts/health-check-prod.sh](../scripts/health-check-prod.sh)
   `--expect-sha <deployed commit> --wait 150` (all six containers, Caddy
   route, worker log; see [Post-Deploy Health Check](#post-deploy-health-check))

When step 5 or 6 fails, the new containers are already running. The script
then prints the commit that ran before the pull and the command to go back
(`bash scripts/rollback-prod.sh <previous commit>`) and exits 1. With
`--auto-rollback` it runs that rollback itself, still holding the deploy
lock, and exits 1 afterwards either way: a rolled back deploy is a failed
deploy. A failed build or migration (steps 2 and 3) never triggers a
rollback, because the old containers were not touched. `FULL_HEALTH_WAIT`
changes the 150 s budget, `SKIP_FULL_HEALTH_CHECK=1` skips step 6.

Before the build the script exports `GIT_SHA=$(git rev-parse HEAD)`;
[docker-compose.prod.yml](../docker-compose.prod.yml) passes it as build arg
to the api/worker/beat images, so `/health` reports the commit of the image
that actually runs.

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

### Post-Deploy Health Check

[scripts/health-check-prod.sh](../scripts/health-check-prod.sh) is the check
the merge rule demands after every deploy and rollback. It is read-only and
exits 0 when everything passes, 1 on a failed check, 2 on a usage error:

- each of `api worker beat frontend postgres redis` (compose project
  `lego-arbitrage`, found by label, so test stacks work too) has exactly one
  container that is `running`, not `unhealthy`, no longer `starting`, and not
  in a restart loop (3 or more restarts with the last start under 10 minutes
  ago; a single old restart is only a `WARN`)
- `http://localhost/lego/health` (through Caddy) answers 200 with
  `"status": "healthy"` and reports the expected commit as `version`
- `docker logs --since 5m` of the worker contains no
  `Traceback (most recent call last)`

```bash
cd /srv/lego-arbitrage
bash scripts/health-check-prod.sh                      # expects git rev-parse HEAD
bash scripts/health-check-prod.sh --expect-sha 671e96c # a specific commit (prefix >= 7)
bash scripts/health-check-prod.sh --wait 150           # retry while containers start
```

`--wait` retries every 5 s. A restart loop or a worker traceback ends it at
once, since neither heals by waiting. Right after `up -d` the API container
stays `starting` until its first Docker healthcheck, 30 s later, so a green
result takes at least that long. Variables (`HEALTH_URL`, `COMPOSE_PROJECT`,
`LOG_SINCE`, `MAX_RESTARTS`, `RESTART_WINDOW`, ...) are listed in the header
of the script; tests: [tests/health-check-prod.test.sh](../tests/health-check-prod.test.sh).

### Endpoints And Logs

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f --tail=200
curl -sS http://127.0.0.1/lego/health
# after a deploy: must print the commit that is checked out
curl -fsS http://127.0.0.1/lego/health | jq -r .version; git rev-parse HEAD
```

`/health` needs no login. It checks PostgreSQL (`SELECT 1`) and Redis
(`PING`) in parallel, each with a 3-second limit (the first request against a
hanging database took about 5 s in the test, while the pooled connection is
torn down):

- `200 {"status": "healthy", "version": "<git sha>", "checks": {"database": "ok", "redis": "ok"}, ...}`
- `503 {"status": "unhealthy", ..., "checks": {"database": "error", "redis": "ok"}}`
  when one of them fails; the reason is only logged (`health.check_failed`).

`version` is the Git commit baked into the image (`GIT_SHA`); an image built
without it (local dev, a manual `docker compose build`) reports `app_version`
(`0.2.0`) instead. The Compose healthcheck of `lego-api-prod` uses the same
endpoint, so the container turns `unhealthy` while the database or Redis is
down. Worker and beat are not part of `/health`; they have their own Compose
healthchecks (next section), and the pipeline health below covers what their
tasks achieve.

### Worker and Beat Healthchecks

`lego-worker-prod` and `lego-beat-prod` run `python -m app.liveness worker`
or `... beat` as Compose healthcheck (every 30 s, 3 retries, 60 s start
period). Both processes touch a file under `/tmp` while they are alive
([backend/app/liveness.py](../backend/app/liveness.py),
[backend/app/tasks/liveness_hooks.py](../backend/app/tasks/liveness_hooks.py)):

- **worker:** a consumer bootstep touches `/tmp/celery-worker.alive` every
  30 s while the worker is connected to Redis. The solo pool blocks that timer
  while a task runs, so `task_prerun` writes `/tmp/celery-worker.busy` and
  `task_postrun` removes it. Unhealthy when the alive file is older than 120 s
  and no task is running, or a single task runs longer than 1 h.
- **beat:** the scheduler (`LivenessScheduler`, the default persistent
  scheduler plus a touch) touches `/tmp/celery-beat.alive` on every tick; beat
  wakes up at least every 60 s (`beat_max_loop_interval`). Unhealthy after
  180 s without a tick.

Limits can be raised per container with `CELERY_LIVENESS_MAX_AGE_WORKER`,
`CELERY_LIVENESS_MAX_AGE_BEAT` and `CELERY_LIVENESS_MAX_TASK_SECONDS`
(seconds, in `backend/.env`). Docker only reports `unhealthy`, it does not
restart the container; the uptime check below turns that into an alarm.

```bash
docker inspect -f '{{.State.Health.Status}}' lego-worker-prod lego-beat-prod
docker exec lego-worker-prod python -m app.liveness worker   # prints the reason
```

### Uptime Check With Telegram Alarm

The pipeline watchdog runs inside the stack and cannot report the stack
itself being down. [scripts/uptime-check.sh](../scripts/uptime-check.sh) runs
outside of it, as a systemd user timer of `sebastian` on victus, every 2
minutes. One run fails when

- `http://localhost/lego/health` does not answer 200 with `"status": "healthy"`
  (covers Caddy, API, PostgreSQL, Redis), or
- one of the services `api worker beat frontend postgres redis` of compose
  project `lego-arbitrage` has no container, is not `running`, or is
  `unhealthy`.

After **2 failed runs in a row** it sends a Telegram message (🔴 with the
causes), repeats it when the causes change or after 6 h, and sends 🟢 once
everything is healthy again. A stopped container is reported within about
4 minutes, a hung worker within about 8 (healthcheck plus two runs). A failed
Telegram send is retried on the next run.

Telegram is called directly with curl, not through the app: the bot token
lives in the `app_settings` table, which is unreachable exactly when the
alarm matters. The check therefore keeps its own copy in
`~/.config/lego-uptime/lego-arbitrage.env` (mode 0600, outside the repo; the
token is handed to curl on stdin, never on the command line).

Install once, from the prod checkout so that deploys update the script:

```bash
cd /srv/lego-arbitrage
bash scripts/install-uptime-check.sh --import-telegram   # copies token + chat id from app_settings
systemctl --user list-timers 'lego-uptime-*'
journalctl --user -u lego-uptime-lego-arbitrage.service -n 20
```

Re-run with `--import-telegram` after changing the bot token in the
dashboard settings. Remove with `--uninstall`. Other knobs (`FAIL_THRESHOLD`,
`REALERT_SECONDS`, `HEALTH_URL`, `SERVICES`, `BACKUP_DIR`,
`BACKUP_MAX_AGE_HOURS`) can go into the same config file; see the header of
the script. With `BACKUP_DIR` set the check also fails on a stale nightly
backup, see [Backup → Alarms](#alarms). State (failure counter) lives in
`~/.local/state/lego-uptime/<project>.state`.

Limit: the check runs on victus itself. If victus is off or has no power, no
alarm is sent. A dead man's switch from outside (e.g. the timer pinging
healthchecks.io, which alarms when the pings stop) would close that gap.

Testing the alarm without touching prod: start a second stack under project
`lego-feedback` (never stop `*-prod` containers for a test), install a timer
for it with `--project lego-feedback --health-url <its health URL>`, stop one
of its containers and wait for the message; afterwards
`--project lego-feedback --uninstall`. The alarm logic itself is covered by
[tests/uptime-check.test.sh](../tests/uptime-check.test.sh) (stubbed docker
and curl), which runs in the verify job.

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

What is backed up, and where (state 21.10.2026):

| What | Where | When | Retention |
|---|---|---|---|
| PostgreSQL dump `lego_<db>_<ts>.sql.gz` | `${DATA_ROOT}/backups` | 03:30, `scripts/backup-db.sh` | `BACKUP_KEEP` (14) |
| Inventory photos `lego_media_<ts>.tar` (`${DATA_ROOT}/media`) | `${DATA_ROOT}/backups` | same run | `BACKUP_MEDIA_KEEP` (7) |
| Mirror of both archive kinds | `BACKUP_REMOTE` (rclone or rsync) | same run | = local rotation |
| Whole `/srv` and `/mnt/HC_Volume_105179687` (without the raw `postgres/` dir) | restic repo on OneDrive, host config `services.restic.backups.onedrive` | ~04:00 | 7 daily, 4 weekly, 6 monthly |

`${DATA_ROOT}` is `/mnt/HC_Volume_105179687/lego-arbitrage`, on the same NVMe
disk as everything else on victus. The local archives alone do not survive a
disk loss; the off-site copies do. The restic job is part of the NixOS host
configuration, runs as root and is not managed from this repo; it encrypts
with its own password. Archives are written with mode 0600: the dump contains
`app_settings` (bot token, API keys).

```bash
# One-off backup (dump + photo archive, verified, rotated, mirrored if BACKUP_REMOTE is set)
bash scripts/backup-db.sh

# List backups
ls -lh /mnt/HC_Volume_105179687/lego-arbitrage/backups

# Restore a dump (DESTRUCTIVE — drops & recreates objects)
CONFIRM=yes bash scripts/restore-db.sh /mnt/HC_Volume_105179687/lego-arbitrage/backups/<file>.sql.gz

# Restore the photos (stack stopped or into an empty DATA_ROOT; entries are media/...)
tar -C /mnt/HC_Volume_105179687/lego-arbitrage -xf /mnt/HC_Volume_105179687/lego-arbitrage/backups/lego_media_<ts>.tar
```

### Schedule

On victus the nightly run comes from the NixOS system cron
(`services.cron.systemCronJobs` in the host configuration), as user sebastian:

```
30 3 * * * sebastian PATH=/run/current-system/sw/bin cd /srv/lego-arbitrage && bash scripts/backup-db.sh >> /mnt/HC_Volume_105179687/lego-arbitrage/backups/cron.log 2>&1
```

On another host use a crontab with the same line (and a PATH that contains
docker, tar, flock and rclone/rsync).

### Off-site target (`BACKUP_REMOTE`)

Set in `.env.prod` (no secrets in the value; credentials live in the rclone
config or the ssh key of the user that runs the backup):

- `BACKUP_REMOTE=rclone:<remote>:<path>` — `rclone sync` of the archives. Use
  a `crypt` remote so the provider never sees the dump in clear text.
- `BACKUP_REMOTE=rsync:[user@]host:/path` — `rsync -a --delete` over ssh, e.g.
  to a Hetzner Storage Box or a second machine.
- `BACKUP_REMOTE=<remote>:<path>` — older form, same as `rclone:`.

The remote mirrors the retained archives (only `lego_<db>_*.sql.gz` and
`lego_media_*.tar`; foreign files on the remote are not touched), so its size
follows the local rotation. A wiped local backup directory is mirrored too on
the next run; history beyond the rotation window comes from the restic
snapshots or from snapshots on the remote side. Empty `BACKUP_REMOTE` is a
warning in the log, not an alarm.

### Alarms

- `backup-db.sh` sends a Telegram message on every failure: 🔴 when the local
  backup failed (step named: `config`, `lock`, `db-dump`, `db-verify`,
  `media-archive`, `media-verify`, `rotation`; exit 1), 🟠 when only the
  off-site mirror failed (exit 2). Token and chat id come from
  `~/.config/lego-uptime/lego-arbitrage.env`, the config of the uptime check
  (`scripts/install-uptime-check.sh --import-telegram`). Without them the
  alarm is only logged (`ALARM NOT SENT` in `cron.log`).
- A cron that does not run at all is caught by the uptime check: with
  `BACKUP_DIR` in its config it fails when the newest dump (or, once one
  exists, the newest photo archive) is older than `BACKUP_MAX_AGE_HOURS`
  (26). `install-uptime-check.sh` enables this for prod on first install from
  `.env.prod`, or explicitly with `--backup-dir <dir>`.
- The restic job has no alarm of its own; `journalctl -u restic-backups-onedrive`
  shows its runs.

### Restore probe

`scripts/restore-probe.sh` proves that the newest backup restores, without
touching prod:

```bash
cd /srv/lego-arbitrage
bash scripts/restore-probe.sh --report /tmp/restore-probe.md
# options: --dump FILE --media FILE --no-media --no-prod --alert
```

It counts the rows of every table from the dump's COPY blocks, restores the
dump into a throwaway `postgres:16-alpine` container (`--network none`, data
on tmpfs, removed afterwards), requires the restored row count of every table
to equal the dump, compares `offers`, `price_records`, `inventory_items` and
`inventory_photos` and the Alembic revision with prod (read-only transaction;
prod keeps growing, so the difference is what was added since the dump), and
resolves every `inventory_photos` row to
`media/inventory_photos/<item_id>/<filename>` in the photo archive of the
same run and in the live directory. Exit 0 = PASS, 1 = FAIL.

Probe log:

| Date | Dump | Result |
|---|---|---|
| 21.10.2026 03:56 | fresh run of the new `backup-db.sh` (609 590 B dump, 185-file photo archive, 83 MB) | PASS: 19 tables, 13 823 rows restored = dump; offers 908, price_records 6 467, inventory_items 125, inventory_photos 185 = prod; Alembic `b6d1f0a4c7e3` = prod; 185/185 photo paths in the archive and live |
| 21.10.2026 03:57 | nightly cron dump `lego_lego_arbitrage_20261021-033001.sql.gz` | PASS, same counts |

Repeat the probe after schema changes and at least monthly; add a line here.

## Rollback

[scripts/rollback-prod.sh](../scripts/rollback-prod.sh) puts the stack back
on an earlier commit, usually the one that ran before the failed deploy
(`deploy-prod.sh` prints it). Always try `--dry-run` first: it resolves the
commit, compares the schema and shows the current state, but changes nothing.

```bash
cd /srv/lego-arbitrage
bash scripts/rollback-prod.sh --dry-run <previous-commit>
bash scripts/rollback-prod.sh <previous-commit>
```

Steps: deploy lock (the same as `deploy-prod.sh`), `git fetch origin`,
refuse a checkout with local changes to tracked files, schema comparison,
`git checkout --detach <commit>`, `docker compose build` with
`GIT_SHA=<commit>` while the old containers keep serving, `up -d`, then
`health-check-prod.sh --expect-sha <commit> --wait 180`. A failed build
restores the previous checkout and leaves the containers alone (exit 1). If
the rolled back stack fails its check too, the script prints the api and
worker logs and exits 1; then roll back further by hand.

The script runs from a private copy of itself and of the health check (in
`$TMPDIR`), because the target commit usually predates both files and
`git checkout` would remove them under the running script.

**Migrations are never rolled back.** The script reads the Alembic revision
from `alembic_version` in the postgres container and compares it with the
revisions in the target commit:

- equal to the target's head: nothing to say
- unknown to the target (`WARN: SCHEMA AHEAD`): the target's revision is
  older than the database's. The old code runs against the newer schema,
  which is fine as long as the migrations followed the backward-compatibility
  rule above. If not: ticket, deliberate `alembic downgrade` by a human, no
  further deploys that night (merge-und-deploy rule)
- older than the target's head (`WARN: SCHEMA BEHIND`): the target expects
  migrations the database does not have; the rollback does not run them

Afterwards the checkout is on a detached HEAD, so `scripts/deploy-prod.sh`
refuses to run until it is back on `main`. Revert the broken merge on main
(`git revert -m 1 <merge-sha>`, PR, green `verify`), then
`git checkout main && bash scripts/deploy-prod.sh`.

Variables for test stacks: `COMPOSE_OVERRIDE` (second compose file),
`COMPOSE_PROJECT` (passed as `-p`), `HEALTH_URL`, `LOCK_FILE`,
`SKIP_FETCH=1`, `HEALTH_WAIT`; full list in the script header. Tests:
[tests/rollback-prod.test.sh](../tests/rollback-prod.test.sh) (real git
repository, stubbed docker and curl).

Manual fallback without the script:

```bash
git fetch --all --tags
git checkout <known-good-commit-or-tag>
GIT_SHA="$(git rev-parse HEAD)" docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
bash scripts/health-check-prod.sh --wait 150   # or: curl -fsS http://127.0.0.1/lego/health
```

### Rollback Probe (21.10.2026)

Run against a separate stack (`-p lego-rbtest`, prod compose file plus an
override without fixed container names and without the Caddy network, API on
`127.0.0.1:18000`, its own data directory), never against `*-prod`:

1. Good commit deployed, `health-check-prod.sh --expect-sha <good> --wait 150`
   green after 7 attempts (API `starting` until its first Docker healthcheck).
2. Commit with `import rbtest_absichtlich_kaputt` at the top of
   `backend/app/main.py` built and started: the API exits with
   `ModuleNotFoundError` and Docker restarts it in a loop. The health check
   fails with `api: restarting (10 restarts)` and
   `health: HTTP 000`, exit 1, and stops waiting at once.
3. `rollback-prod.sh --dry-run <good>`: schema matches, plan printed,
   nothing changed.
4. `rollback-prod.sh <good>`: exit 0 after **36 s** from start to green
   check, of which about 30 s were the wait for the API container's first
   Docker healthcheck; `/health` reported the good commit again.

Not covered by the probe: `deploy-prod.sh --auto-rollback` end to end,
because the script polls the fixed container `lego-api-prod`; that path is
covered by [tests/deploy-prod.test.sh](../tests/deploy-prod.test.sh).

## Catawiki Home Scanner

Prod does not scan Catawiki from its Celery worker (`HOME_RUNNER_PLATFORMS`);
a runner fetches jobs from `/api/remote-scan/runner/job` and posts results
back (see `backend/app/tools/catawiki_home_scan.py`). Akamai blocked the old
Hetzner IP; prod now runs on victus behind a home connection, and a test on
21.10.2026 from inside `lego-api-prod` got category pages and lot details
(shipping, commission) through.

On victus the runner is a systemd user timer that runs the tool **inside the
running api container** (`docker exec`). There is no separate checkout or venv:
runner and API share one `PARSER_VERSION`, so a deploy updates both and the
409 after parser changes cannot happen any more.

Setup (once, from the prod checkout, after the deploy that brought the scripts):

1. In the app: Einstellungen > Catawiki: set `Heimrechner-Token` (at least 24
   random characters), `Scan URLs` (one category URL per line, e.g.
   `https://www.catawiki.com/de/c/375-lego`) and `Automatischer Scan`
   (`daily` = 08:40 Berlin).
2. On victus:

   ```bash
   cd /srv/lego-arbitrage
   bash scripts/install-catawiki-home-scan.sh --import-token
   systemctl --user list-timers 'lego-catawiki-scan-*'
   journalctl --user -u lego-catawiki-scan-lego-arbitrage.service -n 20
   ```

   Without `--import-token` the installer generates a token into
   `~/.config/lego-catawiki-scan/lego-arbitrage.env` (0600), which then has to
   be entered in the app. The installer warns (without printing values) when
   the token differs from `app_settings` or URLs/schedule are missing.
3. Test by hand: `bash scripts/catawiki-home-scan.sh --force --dry-run` scans
   without a job and sends nothing; the button "Catawiki jetzt scannen" in the
   watchlist triggers a real job within 10 minutes.

Alarm: the hourly pipeline watchdog (`check_pipeline_health`) reports
`catawiki_home_runner` when the runner has not polled for more than 1 h
(token and URLs set), and `catawiki_scan` when no scan arrived within 26 h
(`daily`) or 170 h (`weekly`), or the last scan failed with zero lots. Both
use the normal re-alert throttle. Telegram must work for the alarm to arrive.

Uninstall: `bash scripts/install-catawiki-home-scan.sh --uninstall`. The
Windows task (`scripts/install-catawiki-home-scan.ps1`, checkout under
`F:\-=Projekte\Privat\LA-catawiki-scanner`) is retired; only use it if prod
moves back to a host whose IP Akamai blocks. From another machine the runner
needs `LEGO_API_URL=http://<victus LAN IP>/lego`; the old address
`http://178.104.97.121/lego` is dead.

## GitHub Account Note

- Private account: `BastiFasti0811`
- Work account: `conuti-sebastian-willkommen`
- Canonical repo today: `BastiFasti0811/Lego-Arbitrage`

For local development, use remotes that clearly target the intended GitHub
account so Git Credential Manager does not silently reuse the wrong identity.
For the server, prefer a deploy key or a dedicated machine credential instead of
interactive developer credentials.

### Grenzen des Uptime-Checks

- Ein Deploy startet Container neu und kann den Check 2–4 Minuten rot färben; bei zwei Fehlläufen in Folge (Standard) kommt dann ein Fehlalarm samt Entwarnung. Wer das vermeiden will, setzt `FAIL_THRESHOLD` vor dem Deploy kurz höher.
- Der Check läuft auf demselben Host (victus). Fällt der Host aus, schlägt kein Alarm an. Abhilfe wäre ein externer Dead-Man-Switch (z. B. healthchecks.io-Ping).
