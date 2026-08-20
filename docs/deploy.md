# Deployment Runbook

## Goal

Run production from GitHub on the Hetzner host `spm-prod-01` via the `deploy`
user and keep the server as a runtime target instead of an editing machine.
The current source-of-truth repo intentionally remains the private GitHub repo
`BastiFasti0811/Lego-Arbitrage`.

## Recommended Topology

- GitHub remains the source of truth for code, history and rollback.
- The Hetzner server runs the containers and persistent data only.
- Production uses [docker-compose.prod.yml](../docker-compose.prod.yml).
- On `spm-prod-01`, the host already has a shared Caddy instance at
  `/opt/SmartPrepMeal/Caddyfile`.
- The LEGO routes from [infra/Caddyfile](../infra/Caddyfile) are meant to be
  merged into that host-level Caddy config instead of starting a second
  port-80/443 proxy container.
- Local development can continue to use the existing `docker-compose.yml`
  with the local `nginx` proxy.

## First-Time Server Setup

1. SSH in as `deploy@178.104.97.121`.
2. Clone the repository into a stable app directory, for example
   `/srv/lego-arbitrage`.
3. Copy [.env.prod.example](../.env.prod.example) to `.env.prod`.
4. Copy [backend/.env.example](../backend/.env.example) to `backend/.env`.
5. Replace every placeholder secret before the first start.
6. Create the persistent data directories:

```bash
mkdir -p /mnt/HC_Volume_105179687/lego-arbitrage/postgres
mkdir -p /mnt/HC_Volume_105179687/lego-arbitrage/redis
mkdir -p /mnt/HC_Volume_105179687/lego-arbitrage/media
```

## GitHub Deploy Key On The Server

Use a repository deploy key on the server instead of a personal GitHub login.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/lego-arbitrage-deploy -C "deploy@spm-prod-01"
cat ~/.ssh/lego-arbitrage-deploy.pub
```

- Add the printed public key in GitHub as a read-only deploy key for the repo.
- Add this SSH config on the server:

```sshconfig
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/lego-arbitrage-deploy
    IdentitiesOnly yes
```

- Point the server checkout to the SSH remote:

```bash
git remote set-url origin git@github.com:BastiFasti0811/Lego-Arbitrage.git
ssh -T git@github.com
```

- If the canonical repo ever moves later, only the remote URL and deploy key
  target need to change. No such move is planned right now.

## GitHub Actions Setup

The repo now includes [deploy-production.yml](../.github/workflows/deploy-production.yml).

Required GitHub repository secrets:

- `PROD_SSH_KEY`: private SSH key that GitHub Actions uses to reach the Hetzner server
- `PROD_KNOWN_HOSTS`: pinned host key output from
  `ssh-keyscan -H 178.104.97.121`

Optional GitHub repository variables:

- `PROD_HOST`: defaults to `178.104.97.121`
- `PROD_USER`: defaults to `deploy`
- `PROD_APP_DIR`: defaults to `/srv/lego-arbitrage`
- `PROD_URL`: optional environment URL shown in GitHub

The `verify` job also runs on pull requests against `main`, so broken changes
surface before the merge; the `deploy` job runs only on pushes to `main` and
manual dispatches (on PRs it shows as skipped). PR runs use their own
concurrency group and never queue behind a production deploy.

Recommended GitHub environment setup:

- Create an environment named `production`
- Add required reviewers before live deploys if you want a manual approval gate
- Scope the deploy secrets to that environment instead of the whole repo when possible

## Required Config Split

- `.env.prod`: host-level Compose values such as `DATA_ROOT`,
  `POSTGRES_PASSWORD`
- `backend/.env`: application secrets and runtime settings such as dashboard
  auth, Telegram token defaults, AI keys and scraper config
- Inventory-Fotos liegen unter `MEDIA_ROOT`, in Produktion per Compose auf
  `${DATA_ROOT}/media` gemountet

## Deploy

Pushes to `main` trigger [deploy-production.yml](../.github/workflows/deploy-production.yml),
which runs the verify job and then executes the versioned deploy script on the
server. The same script is the supported way to deploy manually, from the repo
root on the server:

```bash
bash scripts/deploy-prod.sh
```

The script first takes a non-blocking lock on
`/tmp/lego-arbitrage-deploy.lock`, so a manual run cannot interleave with a
CI deploy — the second run aborts immediately. It then performs these steps
in order:

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

The PostgreSQL data lives under `${DATA_ROOT}/postgres` on the Hetzner block
volume, which is **not** part of Hetzner's automatic backups. Use the bundled
scripts to take and verify logical dumps.

```bash
# One-off backup (writes a verified, gzipped dump to ${BACKUP_DIR:-${DATA_ROOT}/backups})
bash scripts/backup-db.sh

# List backups
ls -lh /mnt/HC_Volume_105179687/lego-arbitrage/backups

# Restore a dump (DESTRUCTIVE — drops & recreates objects)
CONFIRM=yes bash scripts/restore-db.sh /mnt/HC_Volume_105179687/lego-arbitrage/backups/<file>.sql.gz
```

Schedule it nightly via cron on the server (as the deploy user):

```bash
crontab -e
# Daily 03:30 — adjust the repo path to your checkout. The explicit PATH is
# required because cron's default PATH does not include docker/rclone.
30 3 * * * cd /opt/lego-arbitrage && PATH=/usr/local/bin:/usr/bin:/bin bash scripts/backup-db.sh >> /var/log/lego-backup.log 2>&1
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

Rolling back the code does not downgrade the schema. As long as migrations
follow the backward-compatibility rule above, the previous release runs fine
against the newer schema; a schema downgrade (`alembic downgrade`) is a
deliberate manual step.

## GitHub Account Note

- Private account: `BastiFasti0811`
- Work account: `conuti-sebastian-willkommen`
- Canonical repo today: `BastiFasti0811/Lego-Arbitrage`

For local development, use remotes that clearly target the intended GitHub
account so Git Credential Manager does not silently reuse the wrong identity.
For the server, prefer a deploy key or a dedicated machine credential instead of
interactive developer credentials.
