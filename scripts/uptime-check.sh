#!/usr/bin/env bash
#
# Uptime check for a LEGO-Arbitrage Compose stack, run from OUTSIDE the stack
# (systemd user timer on victus, see scripts/install-uptime-check.sh). It sends
# a Telegram alarm after FAIL_THRESHOLD failed runs in a row, repeats it every
# REALERT_SECONDS or when the failure reason changes, and sends an all-clear
# once the stack is healthy again.
#
# One run checks:
#   1. HEALTH_URL answers 200 with "status": "healthy" (API, PostgreSQL, Redis).
#   2. Every service in SERVICES has a container of compose project
#      COMPOSE_PROJECT that is running and not unhealthy (worker and beat have
#      liveness healthchecks, see backend/app/liveness.py).
#   3. Optional (BACKUP_DIR set): the nightly backup is fresh.
#
# Telegram is called directly with curl: the app (and its database, where the
# dashboard keeps the bot token) may be the thing that is down. Token and chat
# id come from a config file outside the repo (default
# ~/.config/lego-uptime/<project>.env, mode 0600), never from the command line.
#
# Usage: scripts/uptime-check.sh            (one check run; exit 0 ok, 1 failed)
#
# Honoured variables (real environment wins over the config file):
#   UPTIME_CONFIG       config file (KEY=value lines, not shell-evaluated)
#   HEALTH_URL          default http://localhost/lego/health
#   COMPOSE_PROJECT     default lego-arbitrage (prod); test stack: lego-feedback
#   SERVICES            default "api worker beat frontend postgres redis"
#   FAIL_THRESHOLD      failed runs in a row before the alarm (default 2)
#   REALERT_SECONDS     repeat an unchanged alarm after this long (default 21600)
#   HTTP_TIMEOUT        seconds for the health request (default 10)
#   STATE_DIR           default ${XDG_STATE_HOME:-~/.local/state}/lego-uptime
#   STACK_LABEL         name used in messages (default: COMPOSE_PROJECT)
#   BACKUP_DIR          if set: also fail when the newest database dump
#                       (lego_*.sql.gz) there, or the newest photo archive
#                       (lego_media_*.tar, once one exists), is older than
#                       BACKUP_MAX_AGE_HOURS (default 26). Catches a backup cron
#                       that does not run at all; backup-db.sh alarms on its
#                       own failures.
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
#   TELEGRAM_API_BASE   default https://api.telegram.org (tests point it elsewhere)

set -Eeuo pipefail

UPTIME_CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/lego-uptime"
UPTIME_CONFIG="${UPTIME_CONFIG:-${UPTIME_CONFIG_DIR}/${COMPOSE_PROJECT:-lego-arbitrage}.env}"

# Read KEY=value from the config file WITHOUT sourcing it (no code execution,
# tolerant of CRLF), same approach as scripts/backup-db.sh.
cfg_get() {
  [[ -r "${UPTIME_CONFIG}" ]] || return 0
  sed -n "s/^$1=//p" "${UPTIME_CONFIG}" | tail -n1 | tr -d '\r'
}

HEALTH_URL="${HEALTH_URL:-$(cfg_get HEALTH_URL)}"
HEALTH_URL="${HEALTH_URL:-http://localhost/lego/health}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-$(cfg_get COMPOSE_PROJECT)}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-lego-arbitrage}"
SERVICES="${SERVICES:-$(cfg_get SERVICES)}"
SERVICES="${SERVICES:-api worker beat frontend postgres redis}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-$(cfg_get FAIL_THRESHOLD)}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-2}"
REALERT_SECONDS="${REALERT_SECONDS:-$(cfg_get REALERT_SECONDS)}"
REALERT_SECONDS="${REALERT_SECONDS:-21600}"
HTTP_TIMEOUT="${HTTP_TIMEOUT:-$(cfg_get HTTP_TIMEOUT)}"
HTTP_TIMEOUT="${HTTP_TIMEOUT:-10}"
STACK_LABEL="${STACK_LABEL:-$(cfg_get STACK_LABEL)}"
STACK_LABEL="${STACK_LABEL:-${COMPOSE_PROJECT}}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-$(cfg_get TELEGRAM_BOT_TOKEN)}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-$(cfg_get TELEGRAM_CHAT_ID)}"
TELEGRAM_API_BASE="${TELEGRAM_API_BASE:-https://api.telegram.org}"
STATE_DIR="${STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/lego-uptime}"
BACKUP_DIR="${BACKUP_DIR:-$(cfg_get BACKUP_DIR)}"
BACKUP_MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-$(cfg_get BACKUP_MAX_AGE_HOURS)}"
BACKUP_MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"

for num in FAIL_THRESHOLD REALERT_SECONDS HTTP_TIMEOUT BACKUP_MAX_AGE_HOURS; do
  if ! [[ "${!num}" =~ ^[0-9]+$ ]] || (( ${!num} < 1 )); then
    echo "${num} must be a positive integer, got '${!num}'" >&2
    exit 2
  fi
done

mkdir -p "${STATE_DIR}"
STATE_FILE="${STATE_DIR}/${COMPOSE_PROJECT}.state"
LOCK_FILE="${STATE_DIR}/${COMPOSE_PROJECT}.lock"

# A slow run (hanging docker/curl) must not overlap the next timer run.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "[$(date -Is)] previous check still running, skipping" >&2
  exit 0
fi

log() { echo "[$(date -Is)] $*"; }

# ── Checks ───────────────────────────────────────────────
REASONS=()

check_http() {
  local body code
  body="$(mktemp)"
  code="$(curl -sS -o "${body}" -w '%{http_code}' --max-time "${HTTP_TIMEOUT}" "${HEALTH_URL}" 2>/dev/null)" || true
  code="${code:-000}"
  if [[ "${code}" != "200" ]]; then
    REASONS+=("health HTTP ${code}")
  elif ! grep -Eq '"status"[[:space:]]*:[[:space:]]*"healthy"' "${body}"; then
    REASONS+=("health 200 without status healthy")
  fi
  rm -f "${body}"
}

check_containers() {
  local svc ids states
  if ! docker info >/dev/null 2>&1; then
    REASONS+=("docker not reachable")
    return
  fi
  for svc in ${SERVICES}; do
    ids="$(docker ps -aq \
      --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${svc}" 2>/dev/null)" || ids=""
    if [[ -z "${ids}" ]]; then
      REASONS+=("${svc}: no container")
      continue
    fi
    # One line per container: "<status> <health|none>".
    # shellcheck disable=SC2086  # ids is a whitespace-separated id list
    states="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ${ids} 2>/dev/null)" || states=""
    if [[ -z "${states}" ]]; then
      REASONS+=("${svc}: inspect failed")
      continue
    fi
    if ! grep -q '^running ' <<<"${states}"; then
      REASONS+=("${svc}: $(head -n1 <<<"${states}" | cut -d' ' -f1)")
    elif grep -q '^running unhealthy$' <<<"${states}"; then
      REASONS+=("${svc}: unhealthy")
    fi
  done
}

# The reason texts are fixed (no age in hours), otherwise every hour would
# count as a changed failure and repeat the alarm.
check_backup() {
  [[ -n "${BACKUP_DIR}" ]] || return 0
  local limit=$((BACKUP_MAX_AGE_HOURS * 3600)) now newest
  now="$(date +%s)"
  if [[ ! -d "${BACKUP_DIR}" ]]; then
    REASONS+=("backup: ${BACKUP_DIR} missing")
    return
  fi
  newest="$(compgen -G "${BACKUP_DIR}/lego_*.sql.gz" | sort | tail -n1)" || newest=""
  if [[ -z "${newest}" ]]; then
    REASONS+=("backup: no database dump")
  elif (( now - $(stat -c %Y "${newest}") > limit )); then
    REASONS+=("backup: newest database dump older than ${BACKUP_MAX_AGE_HOURS} h")
  fi
  # Photo archives exist only since backup-db.sh archives media; until the
  # first one is written their absence is not a failure.
  newest="$(compgen -G "${BACKUP_DIR}/lego_media_*.tar" | sort | tail -n1)" || newest=""
  if [[ -n "${newest}" ]] && (( now - $(stat -c %Y "${newest}") > limit )); then
    REASONS+=("backup: newest photo archive older than ${BACKUP_MAX_AGE_HOURS} h")
  fi
}

# ── Telegram ─────────────────────────────────────────────
send_telegram() {
  local text="$1"
  if [[ -z "${TELEGRAM_BOT_TOKEN}" || -z "${TELEGRAM_CHAT_ID}" ]]; then
    log "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing in ${UPTIME_CONFIG}; alarm NOT sent: ${text}" >&2
    return 1
  fi
  # The token is part of the URL; pass it via `curl -K -` (stdin) so it never
  # shows up in the process list or in this script's output.
  if printf 'url = "%s/bot%s/sendMessage"\n' "${TELEGRAM_API_BASE}" "${TELEGRAM_BOT_TOKEN}" \
    | curl -sS -o /dev/null --fail --max-time 15 -K - \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${text}" \
        --data "disable_web_page_preview=true" 2>/dev/null; then
    return 0
  fi
  log "Telegram send failed; will retry on the next run" >&2
  return 1
}

# ── State ────────────────────────────────────────────────
FAIL_COUNT=0
ALERTED=0
LAST_ALERT_AT=0
ALERT_REASON=""
FIRST_FAIL_AT=0
if [[ -r "${STATE_FILE}" ]]; then
  while IFS='=' read -r key value; do
    case "${key}" in
      FAIL_COUNT|ALERTED|LAST_ALERT_AT|FIRST_FAIL_AT)
        [[ "${value}" =~ ^[0-9]+$ ]] && printf -v "${key}" '%s' "${value}" ;;
      ALERT_REASON) ALERT_REASON="${value}" ;;
    esac
  done < "${STATE_FILE}"
fi

save_state() {
  local tmp="${STATE_FILE}.tmp"
  {
    echo "FAIL_COUNT=${FAIL_COUNT}"
    echo "ALERTED=${ALERTED}"
    echo "LAST_ALERT_AT=${LAST_ALERT_AT}"
    echo "FIRST_FAIL_AT=${FIRST_FAIL_AT}"
    echo "ALERT_REASON=${ALERT_REASON}"
  } > "${tmp}"
  mv "${tmp}" "${STATE_FILE}"
}

fmt_time() { date -d "@$1" '+%d.%m. %H:%M'; }

# ── Run ──────────────────────────────────────────────────
check_http
check_containers
check_backup

NOW="$(date +%s)"
HOST="$(hostname 2>/dev/null || echo unknown-host)"

if (( ${#REASONS[@]} == 0 )); then
  if (( ALERTED == 1 )); then
    if send_telegram "🟢 Lego-Arbitrage (${STACK_LABEL}@${HOST}) wieder in Ordnung. Gestört seit $(fmt_time "${FIRST_FAIL_AT}"), Ursache war: ${ALERT_REASON}"; then
      log "recovered, all-clear sent"
      ALERTED=0
      ALERT_REASON=""
    fi
  elif (( FAIL_COUNT > 0 )); then
    log "recovered after ${FAIL_COUNT} failed run(s), below the alarm threshold"
  else
    log "ok"
  fi
  FAIL_COUNT=0
  # Keep FIRST_FAIL_AT while the all-clear is still unsent, so a retry reports
  # the real start of the outage.
  (( ALERTED == 1 )) || FIRST_FAIL_AT=0
  save_state
  exit 0
fi

REASON="$(printf '%s; ' "${REASONS[@]}")"
REASON="${REASON%; }"
FAIL_COUNT=$((FAIL_COUNT + 1))
(( FIRST_FAIL_AT == 0 )) && FIRST_FAIL_AT="${NOW}"
log "check failed (${FAIL_COUNT}/${FAIL_THRESHOLD}): ${REASON}" >&2

if (( FAIL_COUNT >= FAIL_THRESHOLD )); then
  SEND=""
  if (( ALERTED == 0 )); then
    SEND="🔴 Lego-Arbitrage (${STACK_LABEL}@${HOST}) gestört: ${REASON}. ${FAIL_COUNT} Prüfungen in Folge fehlgeschlagen, seit $(fmt_time "${FIRST_FAIL_AT}"). Prüfen: ${HEALTH_URL}"
  elif [[ "${REASON}" != "${ALERT_REASON}" ]]; then
    SEND="🔴 Lego-Arbitrage (${STACK_LABEL}@${HOST}) Störung verändert: ${REASON} (vorher: ${ALERT_REASON})"
  elif (( NOW - LAST_ALERT_AT >= REALERT_SECONDS )); then
    SEND="🔴 Lego-Arbitrage (${STACK_LABEL}@${HOST}) weiterhin gestört seit $(fmt_time "${FIRST_FAIL_AT}"): ${REASON}"
  fi
  if [[ -n "${SEND}" ]] && send_telegram "${SEND}"; then
    log "alarm sent"
    ALERTED=1
    LAST_ALERT_AT="${NOW}"
    ALERT_REASON="${REASON}"
  fi
fi

save_state
exit 1
