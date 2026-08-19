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

# Build first, then migrate from the freshly built image (it contains the new
# revision files), then swap containers. A failed migration aborts the deploy
# while the previous containers keep serving; migrations must therefore stay
# backward compatible with the code that is still running.
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" build

echo "Applying database migrations (alembic upgrade head)..."
if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" run --rm -T api alembic upgrade head; then
  echo "Database migration failed. Aborting deploy; the running containers were not touched." >&2
  exit 1
fi

docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --remove-orphans \
  postgres redis api worker beat frontend
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps

for attempt in {1..30}; do
  if docker exec lego-api-prod curl -fsS http://127.0.0.1:8000/health > /dev/null; then
    echo "API healthcheck passed."
    echo "Production deploy finished successfully."
    exit 0
  fi

  sleep 2
done

echo "API healthcheck did not pass within 60 seconds." >&2
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs --tail=100 api >&2
exit 1
