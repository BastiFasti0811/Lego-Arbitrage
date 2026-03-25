# Deployment Runbook

## Goal

Run production from GitHub on the Hetzner host `spm-prod-01` via the `deploy`
user and keep the server as a runtime target instead of an editing machine.

## Recommended Topology

- GitHub remains the source of truth for code, history and rollback.
- The Hetzner server runs the containers and persistent data only.
- Production uses [docker-compose.prod.yml](../docker-compose.prod.yml) and
  [infra/Caddyfile](../infra/Caddyfile).
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

- If the canonical repo later moves into the company account, only the remote URL
  and deploy key target need to change.

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

Recommended GitHub environment setup:

- Create an environment named `production`
- Add required reviewers before live deploys if you want a manual approval gate
- Scope the deploy secrets to that environment instead of the whole repo when possible

## Required Config Split

- `.env.prod`: host-level Compose values such as `DATA_ROOT`,
  `POSTGRES_PASSWORD` and `CADDY_SITE_ADDRESS`
- `backend/.env`: application secrets and runtime settings such as dashboard
  auth, Telegram token defaults, AI keys and scraper config

## Deploy

```bash
git pull --ff-only
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

You can also run the versioned helper from the repo root on the server:

```bash
bash scripts/deploy-prod.sh
```

## Health Checks

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f --tail=200
curl -sS http://127.0.0.1/health
```

- Without a domain, keep `CADDY_SITE_ADDRESS=http://178.104.97.121`.
- After the DNS A record points to `178.104.97.121`, switch
  `CADDY_SITE_ADDRESS` to the real domain so Caddy can terminate HTTPS.

## Rollback

```bash
git fetch --all --tags
git checkout <known-good-commit-or-tag>
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

## GitHub Account Note

- Private account: `BastiFasti0811`
- Work account: `conuti-sebastian-willkommen`

For local development, use remotes that clearly target the intended GitHub
account so Git Credential Manager does not silently reuse the wrong identity.
For the server, prefer a deploy key or a dedicated machine credential instead of
interactive developer credentials.
