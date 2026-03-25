# Infrastructure Notes

## Current Production Server

- Provider: Hetzner Cloud
- Hostname: `spm-prod-01`
- Region: `NBG1`
- IPv4: `178.104.97.121`
- OS: `Ubuntu 24.04 LTS`
- SSH: `root@178.104.97.121`, `deploy@178.104.97.121`
- Auth: SSH key only
- Open ports: `22/tcp`, `80/tcp`, `443/tcp`
- Security: `fail2ban` active, password login disabled
- Container stack: Docker Engine + Docker Compose plugin
- Reverse proxy in production: Caddy
- Persistent volume: `/mnt/HC_Volume_105179687`

## Repo Layout For Infra

- Local development stack: [docker-compose.yml](../docker-compose.yml)
- Production stack: [docker-compose.prod.yml](../docker-compose.prod.yml)
- Shared-Caddy route snippet: [infra/Caddyfile](../infra/Caddyfile)
- Production runbook: [docs/deploy.md](./deploy.md)
- GitHub deployment workflow: [deploy-production.yml](../.github/workflows/deploy-production.yml)

## Operational Checks

- Containers: `docker compose --env-file .env.prod -f docker-compose.prod.yml ps`
- Logs: `docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f --tail=200`
- Local health check: `curl -sS http://127.0.0.1/health`

## Deployment Pattern

```bash
git pull --ff-only
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

## DNS

- Without domain: `http://178.104.97.121`
- With domain: point the A record to `178.104.97.121`
- HTTPS terminates via Caddy

## Git And Server Recommendation

Yes, it makes sense to keep the source in GitHub and run production on a separate server.

- GitHub should stay the source of truth for code history, review, rollback and backups.
- The Hetzner host should stay the runtime target, not the place where code is manually edited long term.
- For a business project, the canonical repo should ideally live in a company-owned GitHub account or org.
- Until the company repo exists, the private repo under `BastiFasti0811` is acceptable, but it should be mirrored or transferred later.
- Use the `deploy` user for rollouts; keep `root` for emergency/admin work only.
- A deploy key or machine credential is cleaner on the server than reusing a developer login.

## Multi-Account Git Note

This machine currently uses a private-account remote pattern and there are two GitHub identities in play:

- Private: `BastiFasti0811`
- Work: `conuti-sebastian-willkommen`

To avoid Git Credential Manager picking the wrong identity, prefer remotes that clearly target the intended account and keep deploy credentials separated from local developer credentials.

## Current Alignment Status

- The repository keeps the local `nginx`-based stack for development only.
- Production now runs the LEGO app stack through `docker-compose.prod.yml` and
  plugs into the already existing host-level Caddy on `spm-prod-01`.
- This keeps local work lightweight while matching the real Hetzner/Caddy
  rollout path.
