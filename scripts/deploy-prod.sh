#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

cd "${REPO_DIR}"

# Serialize deploys on this host: the Actions concurrency group only covers
# CI runs, a manual deploy could still interleave with one. Systems without
# flock (Git Bash, macOS) skip the lock; the prod host always has it.
LOCK_FILE="${LOCK_FILE:-/tmp/lego-arbitrage-deploy.lock}"
if command -v flock > /dev/null 2>&1; then
  exec 9> "${LOCK_FILE}"
  if ! flock -n 9; then
    echo "Another deploy is already running (lock: ${LOCK_FILE}). Aborting." >&2
    exit 1
  fi
else
  echo "flock not found — proceeding without the deploy lock." >&2
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE} in ${REPO_DIR}" >&2
  exit 1
fi

if [[ ! -f "backend/.env" ]]; then
  echo "Missing backend/.env in ${REPO_DIR}" >&2
  exit 1
fi

# Production deploys the main branch only. The checkout on victus is a plain
# working copy: if it ever sits on a feature branch or a detached HEAD,
# `git pull --ff-only` would deploy that branch (or fail half-way). Rollback to
# an older commit follows docs/deploy.md#rollback, not this script.
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
CURRENT_BRANCH="$(git branch --show-current)"
if [[ "${CURRENT_BRANCH}" != "${DEPLOY_BRANCH}" ]]; then
  echo "Checkout is on '${CURRENT_BRANCH:-detached HEAD}', expected '${DEPLOY_BRANCH}'. Aborting deploy." >&2
  exit 1
fi

git pull --ff-only

# Build first, then migrate from the freshly built image (it contains the new
# revision files), then swap containers. A failed migration aborts the deploy
# while the previous containers keep serving; migrations must therefore stay
# backward compatible with the code that is still running.
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" build

echo "Applying database migrations (alembic upgrade head)..."
if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" run --rm -T api alembic upgrade head; then
  echo "Database migration step failed. Aborting deploy; the running app containers were not restarted." >&2
  exit 1
fi

docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --remove-orphans \
  postgres redis api worker beat frontend
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps

for attempt in {1..30}; do
  if docker exec lego-api-prod curl -fsS http://127.0.0.1:8000/health > /dev/null; then
    echo "API healthcheck passed."
    echo "Deployed commit: $(git log -1 --format='%h %s')"
    echo "Production deploy finished successfully."
    exit 0
  fi

  sleep 2
done

echo "API healthcheck did not pass within 60 seconds." >&2
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs --tail=100 api >&2
exit 1
