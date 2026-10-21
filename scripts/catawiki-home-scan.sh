#!/usr/bin/env bash
#
# One Catawiki home-scanner run on victus (systemd user timer, see
# scripts/install-catawiki-home-scan.sh).
#
# Catawiki (Akamai) blocked the old Hetzner IP, so prod never scans Catawiki
# itself (HOME_RUNNER_PLATFORMS): a runner asks /api/remote-scan/runner/job and
# posts the lots back. Since October 2026 prod itself runs on victus behind a
# home connection, so the runner no longer needs its own checkout and venv: it
# runs backend/app/tools/catawiki_home_scan.py INSIDE the running api
# container of the stack. Runner and API therefore always share one
# PARSER_VERSION, and a deploy updates both (no more 409 after parser changes).
#
# The token travels via `docker exec --env-file` (read by the docker client
# from the 0600 config file), never via argv.
#
# Usage: scripts/catawiki-home-scan.sh [--dry-run] [--force]
#        (arguments are passed on to the Python tool)
#
# Honoured variables (real environment wins over the config file):
#   CATAWIKI_SCAN_CONFIG  config file (default
#                         ${XDG_CONFIG_HOME:-~/.config}/lego-catawiki-scan/<project>.env)
#   COMPOSE_PROJECT       default lego-arbitrage (prod)
#   LOCK_FILE             default ${XDG_RUNTIME_DIR:-/tmp}/lego-catawiki-scan-<project>.lock
#
# Config keys: COMPOSE_PROJECT, LEGO_API_URL (default http://127.0.0.1:8000,
# the API as seen from inside the api container), LEGO_REMOTE_SCAN_TOKEN.
#
# Exit codes: 0 ok or nothing to do, 1 scan/API error, 2 setup error
# (no config, no token, no running api container), 75 another run is active.

set -Eeuo pipefail

PROJECT_FROM_ENV="${COMPOSE_PROJECT:-}"
CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/lego-catawiki-scan"
CATAWIKI_SCAN_CONFIG="${CATAWIKI_SCAN_CONFIG:-${CONFIG_DIR}/${PROJECT_FROM_ENV:-lego-arbitrage}.env}"

# KEY=value without sourcing (no code execution, tolerant of CRLF).
cfg_get() {
  [[ -r "${CATAWIKI_SCAN_CONFIG}" ]] || return 0
  sed -n "s/^$1=//p" "${CATAWIKI_SCAN_CONFIG}" | tail -n1 | tr -d '\r'
}

if [[ ! -r "${CATAWIKI_SCAN_CONFIG}" ]]; then
  echo "Config not found: ${CATAWIKI_SCAN_CONFIG} (run scripts/install-catawiki-home-scan.sh)" >&2
  exit 2
fi

COMPOSE_PROJECT="${PROJECT_FROM_ENV:-$(cfg_get COMPOSE_PROJECT)}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-lego-arbitrage}"
if ! [[ "${COMPOSE_PROJECT}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "Invalid compose project name: ${COMPOSE_PROJECT}" >&2
  exit 2
fi

if [[ -z "$(cfg_get LEGO_REMOTE_SCAN_TOKEN)" ]]; then
  echo "LEGO_REMOTE_SCAN_TOKEN missing in ${CATAWIKI_SCAN_CONFIG}" >&2
  exit 2
fi

LOCK_FILE="${LOCK_FILE:-${XDG_RUNTIME_DIR:-/tmp}/lego-catawiki-scan-${COMPOSE_PROJECT}.lock}"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another Catawiki scan of ${COMPOSE_PROJECT} is running; skipping."
  exit 75
fi

api_id="$(docker ps -q \
  --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
  --filter "label=com.docker.compose.service=api" \
  --filter "status=running" | head -n1)"
if [[ -z "${api_id}" ]]; then
  echo "No running api container for compose project ${COMPOSE_PROJECT}" >&2
  exit 2
fi

# Only the two keys the tool reads go into the container environment.
env_file="$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/lego-catawiki-scan.XXXXXX")"
trap 'rm -f "${env_file}"' EXIT
chmod 600 "${env_file}"
api_url="$(cfg_get LEGO_API_URL)"
printf 'LEGO_API_URL=%s\nLEGO_REMOTE_SCAN_TOKEN=%s\n' \
  "${api_url:-http://127.0.0.1:8000}" "$(cfg_get LEGO_REMOTE_SCAN_TOKEN)" > "${env_file}"

set +e
docker exec --env-file "${env_file}" "${api_id}" python -m app.tools.catawiki_home_scan "$@"
status=$?
set -e
# The tool exits 1 on its own errors; anything else (docker, python missing) is
# mapped to 1 as well so the unit shows "failed" either way.
(( status == 0 )) || exit 1
