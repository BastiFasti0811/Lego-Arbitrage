# Infrastructure Notes

## Current Production Server

- Host: `victus` (Sebastian's Linux machine at home), NixOS 26.05
- App directory: `/srv/lego-arbitrage` (owner `sebastian`)
- Network: home LAN only, no public address; on 20.10.2026 `192.168.50.89`
  (DHCP, not fixed)
- Container stack: Docker Engine + Docker Compose plugin
- Reverse proxy: shared Caddy container `smartprepmeal-caddy-1`
  (`/opt/SmartPrepMeal/Caddyfile`), ports 80/443
- Persistent data: `DATA_ROOT=/mnt/HC_Volume_105179687/lego-arbitrage` on the
  local disk (the path name is a leftover from the Hetzner volume)

Until early October 2026 production ran on the Hetzner Cloud host
`spm-prod-01` (`178.104.97.121`, NBG1). That host no longer answers.

## Repo Layout For Infra

- Local development stack: [docker-compose.yml](../docker-compose.yml)
- Production stack: [docker-compose.prod.yml](../docker-compose.prod.yml)
- Shared-Caddy route snippet: [infra/Caddyfile](../infra/Caddyfile)
- Production runbook: [docs/deploy.md](./deploy.md)
- CI workflow (verify only, no deploy): [ci.yml](../.github/workflows/ci.yml)

## Operational Checks

- Containers: `docker compose --env-file .env.prod -f docker-compose.prod.yml ps`
- Logs: `docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f --tail=200`
- Local health check: `curl -sS http://localhost/lego/health`

## Deployment Pattern

```bash
bash scripts/deploy-prod.sh
```

The script builds the new images, applies Alembic migrations from the freshly
built image, then swaps the containers; a failed migration aborts before any
app container is restarted. Details in [deploy.md](deploy.md#deploy). Do not
deploy with a plain `up -d --build` — that skips the migration step.

## Access

- On victus: `http://localhost/lego`
- Home network: `http://<victus LAN IP>/lego`
- No public DNS name and no HTTPS for the app yet

## Git And Server Recommendation

Yes, it makes sense to keep the source in GitHub and run production on a separate server.

- GitHub should stay the source of truth for code history, review, rollback and backups.
- The prod checkout on victus should stay the runtime target, not the place where code is edited.
- The current canonical repo intentionally stays on the private GitHub account
  `BastiFasti0811`.
- A move into a company-owned GitHub account or org is optional and should only
  happen if the team explicitly decides to change ownership later.
- A deploy key or machine credential is cleaner on the server than reusing a developer login.

## Multi-Account Git Note

This machine currently uses a private-account remote pattern and there are two GitHub identities in play:

- Private: `BastiFasti0811`
- Work: `conuti-sebastian-willkommen`
- Canonical repo today: `BastiFasti0811/Lego-Arbitrage`

To avoid Git Credential Manager picking the wrong identity, prefer remotes that clearly target the intended account and keep deploy credentials separated from local developer credentials.

## Current Alignment Status

- The repository keeps the local `nginx`-based stack for development only.
- Production runs the LEGO app stack through `docker-compose.prod.yml` and
  plugs into the already existing host-level Caddy on victus.
