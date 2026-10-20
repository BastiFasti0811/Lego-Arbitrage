#!/usr/bin/env bash
#
# Post-deploy health check for the LEGO-Arbitrage production stack on victus.
# Read-only: it never starts, stops or changes anything.
#
# One run checks:
#   1. Every service in SERVICES has exactly one container of compose project
#      COMPOSE_PROJECT that is running, not unhealthy or still starting, and
#      not in a restart loop (RestartCount >= MAX_RESTARTS while the last
#      start is younger than RESTART_WINDOW seconds).
#   2. HEALTH_URL answers 200 with "status": "healthy" and reports the
#      expected commit as "version" (see --expect-sha).
#   3. The worker log of the last LOG_SINCE contains no Python traceback.
#
# Usage:
#   scripts/health-check-prod.sh [--expect-sha SHA | --no-version-check]
#                                [--wait SECONDS] [--quiet]
#
#   --expect-sha SHA     commit /health must report (full SHA or a prefix of
#                        at least 7 characters). Default: `git rev-parse HEAD`
#                        of the checkout this script lives in (REPO_DIR).
#   --no-version-check   skip the version comparison (status must still be
#                        healthy and a version must be present)
#   --wait SECONDS       repeat the check every WAIT_INTERVAL seconds until it
#                        passes or SECONDS have elapsed (for use right after
#                        `docker compose up`, while containers start). A
#                        restart loop or a worker traceback ends the wait at
#                        once: neither heals by waiting.
#   --quiet              print only the final result line
#
# Exit codes: 0 all checks passed, 1 a check failed, 2 usage/setup error.
#
# Honoured variables:
#   HEALTH_URL       default http://localhost/lego/health (through Caddy)
#   COMPOSE_PROJECT  default lego-arbitrage (prod); test stacks use their own
#   SERVICES         default "api worker beat frontend postgres redis"
#   WORKER_SERVICE   service whose log is scanned (default worker)
#   LOG_SINCE        docker logs --since value (default 5m)
#   MAX_RESTARTS     restarts that count as a loop (default 3)
#   RESTART_WINDOW   seconds since the last start for a loop (default 600)
#   HTTP_TIMEOUT     seconds for the health request (default 10)
#   WAIT_INTERVAL    seconds between attempts with --wait (default 5)
#   REPO_DIR         checkout used for the default expected SHA

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd -- "${SCRIPT_DIR}/.." && pwd)}"

HEALTH_URL="${HEALTH_URL:-http://localhost/lego/health}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-lego-arbitrage}"
SERVICES="${SERVICES:-api worker beat frontend postgres redis}"
WORKER_SERVICE="${WORKER_SERVICE:-worker}"
LOG_SINCE="${LOG_SINCE:-5m}"
MAX_RESTARTS="${MAX_RESTARTS:-3}"
RESTART_WINDOW="${RESTART_WINDOW:-600}"
HTTP_TIMEOUT="${HTTP_TIMEOUT:-10}"
WAIT_INTERVAL="${WAIT_INTERVAL:-5}"

usage() {
  sed -n '/^# Usage:/,/^# Exit codes/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

EXPECT_SHA=""
VERSION_CHECK=1
WAIT_SECONDS=0
QUIET=0
while (( $# > 0 )); do
  case "$1" in
    --expect-sha)
      [[ $# -ge 2 && -n "$2" ]] || usage
      EXPECT_SHA="$2"
      shift 2
      ;;
    --expect-sha=*)
      EXPECT_SHA="${1#*=}"
      [[ -n "${EXPECT_SHA}" ]] || usage
      shift
      ;;
    --no-version-check) VERSION_CHECK=0; shift ;;
    --wait)
      [[ $# -ge 2 ]] || usage
      WAIT_SECONDS="$2"
      shift 2
      ;;
    --wait=*) WAIT_SECONDS="${1#*=}"; shift ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

for num in WAIT_SECONDS MAX_RESTARTS RESTART_WINDOW HTTP_TIMEOUT WAIT_INTERVAL; do
  if ! [[ "${!num}" =~ ^[0-9]+$ ]]; then
    echo "${num} must be a non-negative integer, got '${!num}'" >&2
    exit 2
  fi
done

if (( VERSION_CHECK )) && [[ -z "${EXPECT_SHA}" ]]; then
  if ! EXPECT_SHA="$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null)" || [[ -z "${EXPECT_SHA}" ]]; then
    echo "Cannot determine the expected commit (git rev-parse HEAD in ${REPO_DIR}); pass --expect-sha or --no-version-check." >&2
    exit 2
  fi
fi
if (( VERSION_CHECK )) && ! [[ "${EXPECT_SHA}" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "--expect-sha must be a hex commit id (7-40 chars), got '${EXPECT_SHA}'" >&2
  exit 2
fi

# ── Checks (fill FAILS / WARNS / OKS) ───────────────────
FAILS=()
WARNS=()
OKS=()
WORKER_TRACE=""
# Set when a failure cannot heal by waiting (restart loop, traceback): --wait
# stops early instead of burning its whole budget on a broken release.
FATAL=0

now_epoch() { date +%s; }

# Seconds since an RFC 3339 timestamp from docker inspect; empty if unparsable.
age_of() {
  local started
  started="$(date -d "$1" +%s 2>/dev/null)" || return 0
  echo $(( $(now_epoch) - started ))
}

check_containers() {
  local svc ids count line status health restarts started age
  if ! docker info >/dev/null 2>&1; then
    FAILS+=("docker not reachable")
    return
  fi
  for svc in ${SERVICES}; do
    ids="$(docker ps -aq \
      --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${svc}" 2>/dev/null)" || ids=""
    if [[ -z "${ids}" ]]; then
      FAILS+=("${svc}: no container")
      continue
    fi
    count="$(wc -w <<<"${ids}")"
    if (( count > 1 )); then
      FAILS+=("${svc}: ${count} containers (expected 1)")
      continue
    fi
    line="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}} {{.State.StartedAt}}' "${ids}" 2>/dev/null)" || line=""
    if [[ -z "${line}" ]]; then
      FAILS+=("${svc}: inspect failed")
      continue
    fi
    read -r status health restarts started <<<"${line}"
    age="$(age_of "${started}")"
    if [[ "${status}" != "running" ]]; then
      FAILS+=("${svc}: ${status} (${restarts} restarts)")
      if [[ "${restarts}" =~ ^[0-9]+$ ]] && (( restarts >= MAX_RESTARTS )); then
        FATAL=1
      fi
      continue
    fi
    if [[ "${health}" == "unhealthy" ]]; then
      FAILS+=("${svc}: unhealthy")
      continue
    fi
    if [[ "${health}" == "starting" ]]; then
      FAILS+=("${svc}: healthcheck still starting")
      continue
    fi
    if [[ "${restarts}" =~ ^[0-9]+$ ]] && (( restarts > 0 )); then
      if (( restarts >= MAX_RESTARTS )) && [[ -n "${age}" ]] && (( age < RESTART_WINDOW )); then
        FAILS+=("${svc}: restart loop (${restarts} restarts, last start ${age}s ago)")
        FATAL=1
        continue
      fi
      WARNS+=("${svc}: ${restarts} restart(s) since creation, running for ${age:-?}s")
    fi
    if [[ "${health}" == "none" ]]; then OKS+=("${svc}: running"); else OKS+=("${svc}: running (${health})"); fi
  done
}

check_http() {
  local body code status version
  body="$(mktemp)"
  code="$(curl -sS -o "${body}" -w '%{http_code}' --max-time "${HTTP_TIMEOUT}" "${HEALTH_URL}" 2>/dev/null)" || true
  code="${code:-000}"
  status="$(sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${body}" | head -n1)"
  version="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${body}" | head -n1)"
  rm -f "${body}"
  if [[ "${code}" != "200" ]]; then
    FAILS+=("health: HTTP ${code} from ${HEALTH_URL}${status:+ (status ${status})}")
    return
  fi
  if [[ "${status}" != "healthy" ]]; then
    FAILS+=("health: 200 but status '${status:-missing}'")
    return
  fi
  if [[ -z "${version}" ]]; then
    FAILS+=("health: no version reported")
    return
  fi
  if (( VERSION_CHECK )) && [[ "${version}" != "${EXPECT_SHA}"* ]]; then
    FAILS+=("health: version ${version}, expected ${EXPECT_SHA}")
    return
  fi
  OKS+=("health: healthy, version ${version}")
}

check_worker_log() {
  local id logs hits
  WORKER_TRACE=""
  id="$(docker ps -aq \
    --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
    --filter "label=com.docker.compose.service=${WORKER_SERVICE}" 2>/dev/null | head -n1)" || id=""
  if [[ -z "${id}" ]]; then
    # The container check already reports the missing worker.
    return
  fi
  if ! logs="$(docker logs --since "${LOG_SINCE}" "${id}" 2>&1)"; then
    FAILS+=("${WORKER_SERVICE} log: docker logs failed")
    return
  fi
  hits="$(grep -c 'Traceback (most recent call last)' <<<"${logs}" || true)"
  if (( hits > 0 )); then
    FAILS+=("${WORKER_SERVICE} log: ${hits} traceback(s) in the last ${LOG_SINCE}")
    # Keep the start of the first traceback for the report.
    WORKER_TRACE="$(grep -m1 -A 12 'Traceback (most recent call last)' <<<"${logs}")"
    FATAL=1
    return
  fi
  OKS+=("${WORKER_SERVICE} log: no traceback in the last ${LOG_SINCE}")
}

run_checks() {
  FAILS=()
  WARNS=()
  OKS=()
  FATAL=0
  check_containers
  check_http
  check_worker_log
}

print_report() {
  local item
  (( QUIET )) && return 0
  for item in "${OKS[@]}"; do echo "OK    ${item}"; done
  for item in "${WARNS[@]}"; do echo "WARN  ${item}"; done
  for item in "${FAILS[@]}"; do echo "FAIL  ${item}"; done
  if [[ -n "${WORKER_TRACE}" ]]; then
    echo "----- first worker traceback -----"
    echo "${WORKER_TRACE}"
    echo "----------------------------------"
  fi
}

join_fails() {
  local IFS=$'\x1f' joined
  joined="${FAILS[*]}"
  echo "${joined//$'\x1f'/; }"
}

target_desc() {
  if (( VERSION_CHECK )); then echo "version ${EXPECT_SHA}"; else echo "no version check"; fi
}

deadline=$(( $(now_epoch) + WAIT_SECONDS ))
attempt=0
while :; do
  attempt=$(( attempt + 1 ))
  run_checks
  if (( ${#FAILS[@]} == 0 )); then
    print_report
    echo "health-check: OK (project ${COMPOSE_PROJECT}, $(target_desc), attempt ${attempt})"
    exit 0
  fi
  if (( FATAL )); then
    (( QUIET )) || echo "attempt ${attempt}: failure will not heal by waiting, giving up early" >&2
    break
  fi
  if (( $(now_epoch) + WAIT_INTERVAL > deadline )); then
    break
  fi
  (( QUIET )) || echo "attempt ${attempt}: $(join_fails) — retrying in ${WAIT_INTERVAL}s" >&2
  sleep "${WAIT_INTERVAL}"
done

print_report
echo "health-check: FAILED (project ${COMPOSE_PROJECT}, $(target_desc), attempt ${attempt}): $(join_fails)" >&2
exit 1
