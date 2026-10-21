#!/usr/bin/env bash
#
# Installs scripts/catawiki-home-scan.sh as a systemd USER timer (default every
# 10 minutes) for one compose project and prepares its config file. Replaces
# the Windows task of scripts/install-catawiki-home-scan.ps1 since prod runs on
# victus. Run it as the user that runs the stack (on victus: sebastian, linger
# is enabled, so the timer also runs without a login session). No root needed.
#
# Install from the prod checkout (/srv/lego-arbitrage): the unit points at the
# script of the checkout it was installed from, and the scan code itself comes
# from the running api container, so a deploy updates both.
#
# Usage:
#   scripts/install-catawiki-home-scan.sh [options]
#
# Options:
#   --project NAME     compose project (default lego-arbitrage = prod)
#   --interval MIN     minutes between runs, 1-59 (default 10)
#   --import-token     copy remote_scan_token from the app_settings table of the
#                      running stack (value is never printed). Without it, an
#                      existing token in the config is kept, else a new one is
#                      generated and has to be entered in the app.
#   --no-start         write the files but do not enable/start the timer
#   --uninstall        stop and remove the timer of --project (config kept)
#
# Files:
#   ~/.config/lego-catawiki-scan/<project>.env           config, mode 0600
#   ~/.config/systemd/user/lego-catawiki-scan-<project>.service/.timer
#
# Logs: journalctl --user -u lego-catawiki-scan-<project>.service
# The pipeline watchdog alarms via Telegram when the runner stays silent for
# more than an hour or no scan arrives on schedule (evaluate_catawiki_scan).

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_SCRIPT="${SCRIPT_DIR}/catawiki-home-scan.sh"

PROJECT="lego-arbitrage"
INTERVAL=10
IMPORT_TOKEN=0
START=1
UNINSTALL=0

while (( $# > 0 )); do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --import-token) IMPORT_TOKEN=1; shift ;;
    --no-start) START=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if ! [[ "${PROJECT}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "Invalid compose project name: ${PROJECT}" >&2
  exit 2
fi
if ! [[ "${INTERVAL}" =~ ^[0-9]+$ ]] || (( INTERVAL < 1 || INTERVAL > 59 )); then
  echo "--interval must be 1-59 minutes" >&2
  exit 2
fi

CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/lego-catawiki-scan"
CONFIG_FILE="${CONFIG_DIR}/${PROJECT}.env"
UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
UNIT="lego-catawiki-scan-${PROJECT}"

if (( UNINSTALL == 1 )); then
  systemctl --user disable --now "${UNIT}.timer" 2>/dev/null || true
  rm -f "${UNIT_DIR}/${UNIT}.timer" "${UNIT_DIR}/${UNIT}.service"
  systemctl --user daemon-reload
  echo "Timer ${UNIT}.timer removed. Config kept: ${CONFIG_FILE}"
  exit 0
fi

# ── Config file (0600, KEY=value, read without sourcing) ──
umask 077
mkdir -p "${CONFIG_DIR}"
touch "${CONFIG_FILE}"
chmod 600 "${CONFIG_FILE}"

set_cfg() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp "${CONFIG_DIR}/.cfg.XXXXXX")"
  grep -v "^${key}=" "${CONFIG_FILE}" > "${tmp}" || true
  printf '%s=%s\n' "${key}" "${value}" >> "${tmp}"
  mv "${tmp}" "${CONFIG_FILE}"
}
cfg_value() { sed -n "s/^$1=//p" "${CONFIG_FILE}" | tail -n1 | tr -d '\r'; }
trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  printf '%s' "${value%"${value##*[![:space:]]}"}"
}

set_cfg COMPOSE_PROJECT "${PROJECT}"
[[ -n "$(cfg_value LEGO_API_URL)" ]] || set_cfg LEGO_API_URL "http://127.0.0.1:8000"

pg_id="$(docker ps -q \
  --filter "label=com.docker.compose.project=${PROJECT}" \
  --filter "label=com.docker.compose.service=postgres" | head -n1)"
read_setting() {
  # Key names are fixed literals, no user input reaches the SQL. Read-only.
  [[ -n "${pg_id}" ]] || return 0
  docker exec "${pg_id}" sh -c \
    "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -At -c \"select value from app_settings where key = '$1'\"" \
    | tr -d '\r\n'
}

if (( IMPORT_TOKEN == 1 )); then
  if [[ -z "${pg_id}" ]]; then
    echo "No running postgres container for project ${PROJECT}" >&2
    exit 1
  fi
  token="$(trim "$(read_setting remote_scan_token)")"
  if (( ${#token} < 24 )); then
    echo "remote_scan_token is not set (or shorter than 24 characters) in app_settings of ${PROJECT}." >&2
    echo "Set it in the app (Einstellungen > Catawiki > Heimrechner-Token) or re-run without --import-token." >&2
    exit 1
  fi
  set_cfg LEGO_REMOTE_SCAN_TOKEN "${token}"
  unset token
  echo "Token imported from app_settings of ${PROJECT} into ${CONFIG_FILE}"
elif [[ -z "$(cfg_value LEGO_REMOTE_SCAN_TOKEN)" ]]; then
  # 32 random bytes, URL-safe base64 (43 characters), like the Windows installer.
  set_cfg LEGO_REMOTE_SCAN_TOKEN "$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')"
  echo "New token generated in ${CONFIG_FILE}. Enter the same value in the app under"
  echo "  Einstellungen > Catawiki > Heimrechner-Token"
  echo "Show it with: sed -n 's/^LEGO_REMOTE_SCAN_TOKEN=//p' ${CONFIG_FILE}"
fi

# Plausibility against the running stack, without printing any value.
if [[ -n "${pg_id}" ]]; then
  db_token="$(trim "$(read_setting remote_scan_token)")"
  if [[ -z "${db_token}" ]]; then
    echo "WARNING: app_settings has no remote_scan_token yet: prod answers 401 until it is set." >&2
  elif [[ "${db_token}" != "$(cfg_value LEGO_REMOTE_SCAN_TOKEN)" ]]; then
    echo "WARNING: token in ${CONFIG_FILE} differs from app_settings: prod answers 401." >&2
    echo "         Re-run with --import-token to take the app's value." >&2
  fi
  unset db_token
  if [[ -z "$(read_setting catawiki_scan_urls | tr -d '[:space:]')" ]]; then
    echo "NOTE: catawiki_scan_urls is empty: the runner polls, but there is nothing to scan." >&2
  fi
  frequency="$(read_setting catawiki_scan_frequency)"
  if [[ "${frequency:-off}" == "off" ]]; then
    echo "NOTE: catawiki_scan_frequency is off: scans only run via the button in the watchlist." >&2
  fi
else
  echo "WARNING: no running postgres container for ${PROJECT}; token and scan URLs not checked." >&2
fi

# ── systemd user units ───────────────────────────────────
umask 022
mkdir -p "${UNIT_DIR}"

# Fixed PATH for the unit: on NixOS docker and flock live under
# /run/current-system/sw/bin, not /usr/bin; the rest covers other distros.
UNIT_PATH="/run/wrappers/bin:/run/current-system/sw/bin:/etc/profiles/per-user/${USER:-$(id -un)}/bin:${HOME}/.nix-profile/bin:/usr/local/bin:/usr/bin:/bin"
for tool in docker flock; do
  if ! PATH="${UNIT_PATH}" command -v "${tool}" >/dev/null; then
    echo "${tool} not found in the unit PATH (${UNIT_PATH})" >&2
    exit 1
  fi
done
BASH_BIN="$(PATH="${UNIT_PATH}" command -v bash)"

cat > "${UNIT_DIR}/${UNIT}.service" <<EOF
[Unit]
Description=Lego-Arbitrage Catawiki home scan (${PROJECT})

[Service]
Type=oneshot
Environment=COMPOSE_PROJECT=${PROJECT}
Environment="CATAWIKI_SCAN_CONFIG=${CONFIG_FILE}"
Environment=PATH=${UNIT_PATH}
ExecStart=${BASH_BIN} "${RUN_SCRIPT}"
# 75 = previous run still active (manual run); not a failure.
SuccessExitStatus=75
# A scan with lot details takes a few minutes; the API lease is 60 minutes.
TimeoutStartSec=35min
EOF

cat > "${UNIT_DIR}/${UNIT}.timer" <<EOF
[Unit]
Description=Lego-Arbitrage Catawiki home scan every ${INTERVAL} minutes (${PROJECT})

[Timer]
OnBootSec=3min
OnCalendar=*-*-* *:03/${INTERVAL}:00
AccuracySec=30s
Persistent=false

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
if (( START == 1 )); then
  systemctl --user enable --now "${UNIT}.timer"
  echo "Timer ${UNIT}.timer active. Logs: journalctl --user -u ${UNIT}.service"
else
  echo "Units written; start with: systemctl --user enable --now ${UNIT}.timer"
fi
