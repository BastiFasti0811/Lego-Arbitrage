#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

cd "${REPO_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE} in ${REPO_DIR}" >&2
  exit 1
fi

if [[ ! -f "backend/.env" ]]; then
  echo "Missing backend/.env in ${REPO_DIR}" >&2
  exit 1
fi

git pull --ff-only

# Update the application services first, then recycle Caddy separately so
# repeat deployments do not hit a transient port-80 bind conflict.
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --build --remove-orphans \
  postgres redis api worker beat frontend
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" rm -sf caddy >/dev/null 2>&1 || true
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --no-deps caddy
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps
curl -fsS http://127.0.0.1/health > /dev/null

echo "Production deploy finished successfully."
