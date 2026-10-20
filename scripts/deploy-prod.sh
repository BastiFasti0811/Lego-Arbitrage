#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

# --auto-rollback: when the new containers fail the health checks, roll back
# to the commit that was checked out before the pull (scripts/rollback-prod.sh).
AUTO_ROLLBACK="${AUTO_ROLLBACK:-0}"
for arg in "$@"; do
  case "${arg}" in
    --auto-rollback) AUTO_ROLLBACK=1 ;;
    *) echo "Unknown argument: ${arg} (supported: --auto-rollback)" >&2; exit 2 ;;
  esac
done

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
# an older commit uses scripts/rollback-prod.sh, which leaves a detached HEAD.
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
CURRENT_BRANCH="$(git branch --show-current)"
if [[ "${CURRENT_BRANCH}" != "${DEPLOY_BRANCH}" ]]; then
  echo "Checkout is on '${CURRENT_BRANCH:-detached HEAD}', expected '${DEPLOY_BRANCH}'. Aborting deploy." >&2
  exit 1
fi

# Commit that runs before this deploy: the rollback target if the new release
# fails its health checks.
PREV_SHA="$(git rev-parse HEAD)"

git pull --ff-only

# The commit being deployed. It is baked into the backend images as a build
# arg (docker-compose.prod.yml -> GIT_SHA) and reported by /health as
# "version"; the health poll below only succeeds once the API reports it, so
# a deploy that silently kept the old containers fails loudly.
GIT_SHA="$(git rev-parse HEAD)"
if [[ -z "${GIT_SHA}" ]]; then
  echo "Could not determine the deployed commit (git rev-parse HEAD). Aborting deploy." >&2
  exit 1
fi
export GIT_SHA

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

# Called once the new containers are up but fail a check: name the rollback
# target, and with --auto-rollback run it (the deploy lock stays held).
fail_after_swap() {
  echo "Deploy of ${GIT_SHA} failed its health checks. Previous commit: ${PREV_SHA}" >&2
  if [[ "${PREV_SHA}" == "${GIT_SHA}" ]]; then
    echo "Nothing to roll back to: the previous commit is the deployed one." >&2
    exit 1
  fi
  if [[ "${AUTO_ROLLBACK}" == "1" ]]; then
    echo "Rolling back to ${PREV_SHA} (--auto-rollback)..." >&2
    if DEPLOY_LOCK_HELD=1 bash scripts/rollback-prod.sh "${PREV_SHA}"; then
      echo "Rolled back to ${PREV_SHA}. The deploy of ${GIT_SHA} counts as FAILED." >&2
    else
      echo "Rollback to ${PREV_SHA} failed as well. Manual action needed (docs/deploy.md#rollback)." >&2
    fi
  else
    echo "Roll back with: bash scripts/rollback-prod.sh ${PREV_SHA}" >&2
  fi
  exit 1
}

# /health answers 200 only when PostgreSQL and Redis are reachable (503
# otherwise, which `curl -f` turns into a failure) and must report GIT_SHA.
HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-30}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"
health_body=""
api_ok=0
for (( attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++ )); do
  if health_body="$(docker exec lego-api-prod curl -fsS http://127.0.0.1:8000/health)"; then
    if [[ "${health_body}" == *"\"version\":\"${GIT_SHA}\""* ]]; then
      echo "API healthcheck passed (version ${GIT_SHA})."
      api_ok=1
      break
    fi
  else
    health_body=""
  fi

  sleep "${HEALTH_INTERVAL}"
done

if (( ! api_ok )); then
  if [[ -n "${health_body}" ]]; then
    echo "API is healthy but does not report the deployed commit ${GIT_SHA}: ${health_body}" >&2
  else
    echo "API healthcheck did not pass within $(( HEALTH_ATTEMPTS * HEALTH_INTERVAL )) seconds." >&2
  fi
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs --tail=100 api >&2
  fail_after_swap
fi

# Full post-deploy check: all six containers without restart loop, /lego/health
# through Caddy with the new version, worker log without traceback. Worker and
# beat need up to their 60 s start period before their healthcheck turns green.
FULL_HEALTH_WAIT="${FULL_HEALTH_WAIT:-150}"
if [[ "${SKIP_FULL_HEALTH_CHECK:-0}" == "1" ]]; then
  echo "Full health check skipped (SKIP_FULL_HEALTH_CHECK=1)."
elif ! bash scripts/health-check-prod.sh --expect-sha "${GIT_SHA}" --wait "${FULL_HEALTH_WAIT}"; then
  fail_after_swap
fi

echo "Deployed commit: $(git log -1 --format='%h %s')"
echo "Production deploy finished successfully."
exit 0
