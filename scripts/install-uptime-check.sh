#!/usr/bin/env bash
#
# Installs scripts/uptime-check.sh as a systemd USER timer (every 2 minutes)
# for one compose project, and prepares its config file. Run it as the user
# that runs the stack (on victus: sebastian, linger is enabled there, so the
# timer also runs without a login session). No root needed.
#
# Usage:
#   scripts/install-uptime-check.sh [options]
#
# Options:
#   --project NAME        compose project to watch (default lego-arbitrage = prod)
#   --health-url URL      health URL (default http://localhost/lego/health)
#   --label TEXT          stack name in the Telegram messages (default: project)
#   --services "a b"      services that must run (default: all six)
#   --backup-dir DIR      also alarm when the nightly backup in DIR is stale
#                         (see BACKUP_DIR in uptime-check.sh). Default for the
#                         prod project on first install: BACKUP_DIR, else
#                         ${DATA_ROOT}/backups, from ../.env.prod. "" disables.
#   --import-telegram     copy bot token and chat id from the app_settings table
#                         of a running stack (values are never printed)
#   --telegram-from NAME  project whose database holds the Telegram settings
#                         (default: lego-arbitrage)
#   --no-start            write the files but do not enable/start the timer
#   --uninstall           stop and remove the timer of --project (config kept)
#
# Files:
#   ~/.config/lego-uptime/<project>.env               config, mode 0600
#   ~/.config/systemd/user/lego-uptime-<project>.service/.timer
#   ~/.local/state/lego-uptime/<project>.state        failure counter
#
# One unit pair per project, each pointing at the checkout it was installed
# from: install the prod check from /srv/lego-arbitrage, so deploys update it.
#
# Logs: journalctl --user -u lego-uptime-<project>.service

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHECK_SCRIPT="${SCRIPT_DIR}/uptime-check.sh"

PROJECT="lego-arbitrage"
HEALTH_URL=""
LABEL=""
SERVICES=""
BACKUP_DIR_OPT=""
BACKUP_DIR_SET=0
IMPORT_TELEGRAM=0
TELEGRAM_FROM="lego-arbitrage"
START=1
UNINSTALL=0

while (( $# > 0 )); do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --health-url) HEALTH_URL="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --services) SERVICES="$2"; shift 2 ;;
    --backup-dir) BACKUP_DIR_OPT="$2"; BACKUP_DIR_SET=1; shift 2 ;;
    --import-telegram) IMPORT_TELEGRAM=1; shift ;;
    --telegram-from) TELEGRAM_FROM="$2"; shift 2 ;;
    --no-start) START=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,36p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if ! [[ "${PROJECT}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "Invalid compose project name: ${PROJECT}" >&2
  exit 2
fi

CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/lego-uptime"
CONFIG_FILE="${CONFIG_DIR}/${PROJECT}.env"
UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
UNIT="lego-uptime-${PROJECT}"

if (( UNINSTALL == 1 )); then
  systemctl --user disable --now "${UNIT}.timer" 2>/dev/null || true
  rm -f "${UNIT_DIR}/${UNIT}.timer" "${UNIT_DIR}/${UNIT}.service"
  systemctl --user daemon-reload
  echo "Timer ${UNIT}.timer removed. Config and state kept: ${CONFIG_FILE}"
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

set_cfg COMPOSE_PROJECT "${PROJECT}"
[[ -n "${HEALTH_URL}" ]] && set_cfg HEALTH_URL "${HEALTH_URL}"
[[ -n "${LABEL}" ]] && set_cfg STACK_LABEL "${LABEL}"
[[ -n "${SERVICES}" ]] && set_cfg SERVICES "${SERVICES}"

# Backup freshness: explicit option wins; otherwise the prod project gets the
# backup directory of the checkout's .env.prod once (a later manual change of
# BACKUP_DIR in the config file is kept on re-install).
if (( BACKUP_DIR_SET == 1 )); then
  set_cfg BACKUP_DIR "${BACKUP_DIR_OPT}"
elif [[ "${PROJECT}" == "lego-arbitrage" ]] && ! grep -q '^BACKUP_DIR=' "${CONFIG_FILE}"; then
  prod_env="${SCRIPT_DIR}/../.env.prod"
  if [[ -r "${prod_env}" ]]; then
    env_value() { sed -n "s/^$1=//p" "${prod_env}" | tail -n1 | tr -d '\r'; }
    detected="$(env_value BACKUP_DIR)"
    [[ -z "${detected}" && -n "$(env_value DATA_ROOT)" ]] && detected="$(env_value DATA_ROOT)/backups"
    if [[ -n "${detected}" ]]; then
      set_cfg BACKUP_DIR "${detected}"
      echo "Backup freshness check enabled for ${detected}"
    fi
  fi
fi

if (( IMPORT_TELEGRAM == 1 )); then
  pg_id="$(docker ps -q \
    --filter "label=com.docker.compose.project=${TELEGRAM_FROM}" \
    --filter "label=com.docker.compose.service=postgres" | head -n1)"
  if [[ -z "${pg_id}" ]]; then
    echo "No running postgres container for project ${TELEGRAM_FROM}" >&2
    exit 1
  fi
  read_setting() {
    # Key names are fixed literals, no user input reaches the SQL.
    docker exec "${pg_id}" sh -c \
      "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -At -c \"select value from app_settings where key = '$1'\"" \
      | tr -d '\r\n'
  }
  token="$(read_setting telegram_bot_token)"
  chat_id="$(read_setting telegram_chat_id)"
  if [[ -z "${token}" || -z "${chat_id}" ]]; then
    echo "telegram_bot_token/telegram_chat_id not set in app_settings of ${TELEGRAM_FROM}" >&2
    exit 1
  fi
  set_cfg TELEGRAM_BOT_TOKEN "${token}"
  set_cfg TELEGRAM_CHAT_ID "${chat_id}"
  unset token chat_id
  echo "Telegram settings imported from ${TELEGRAM_FROM} into ${CONFIG_FILE}"
fi

cfg_value() { sed -n "s/^$1=//p" "${CONFIG_FILE}" | tail -n1 | tr -d '\r'; }

if ! grep -q '^TELEGRAM_BOT_TOKEN=.' "${CONFIG_FILE}" || ! grep -q '^TELEGRAM_CHAT_ID=.' "${CONFIG_FILE}"; then
  echo "WARNING: ${CONFIG_FILE} has no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID; alarms are only logged." >&2
  echo "         Re-run with --import-telegram or add both keys by hand." >&2
else
  # getMe validates the token without sending a message. Token via stdin only.
  if ! printf 'url = "https://api.telegram.org/bot%s/getMe"\n' "$(cfg_value TELEGRAM_BOT_TOKEN)" \
      | curl -sS --fail -o /dev/null --max-time 15 -K - 2>/dev/null; then
    echo "WARNING: Telegram rejected the bot token (getMe failed): alarms will not arrive." >&2
    echo "         Create a new token with @BotFather, save it in the dashboard settings," >&2
    echo "         then re-run with --import-telegram." >&2
  fi
  # A chat id is numeric (user/group, groups start with -) or @channelname.
  if ! [[ "$(cfg_value TELEGRAM_CHAT_ID)" =~ ^(-?[0-9]+|@[A-Za-z0-9_]{5,})$ ]]; then
    echo "WARNING: TELEGRAM_CHAT_ID is neither a numeric chat id nor an @channel name;" >&2
    echo "         a bot username does not work. Send the bot a message and read the id" >&2
    echo "         from https://api.telegram.org/bot<token>/getUpdates (message.chat.id)." >&2
  fi
fi

# ── systemd user units ───────────────────────────────────
umask 022
mkdir -p "${UNIT_DIR}"

# Fixed PATH for the unit: on NixOS docker, curl and flock live under
# /run/current-system/sw/bin, not /usr/bin; the rest covers other distros.
UNIT_PATH="/run/wrappers/bin:/run/current-system/sw/bin:/etc/profiles/per-user/${USER:-$(id -un)}/bin:${HOME}/.nix-profile/bin:/usr/local/bin:/usr/bin:/bin"
for tool in docker curl flock; do
  if ! PATH="${UNIT_PATH}" command -v "${tool}" >/dev/null; then
    echo "${tool} not found in the unit PATH (${UNIT_PATH})" >&2
    exit 1
  fi
done
BASH_BIN="$(PATH="${UNIT_PATH}" command -v bash)"
cat > "${UNIT_DIR}/${UNIT}.service" <<EOF
[Unit]
Description=Lego-Arbitrage uptime check (${PROJECT})

[Service]
Type=oneshot
Environment=COMPOSE_PROJECT=${PROJECT}
Environment=PATH=${UNIT_PATH}
ExecStart=${BASH_BIN} "${CHECK_SCRIPT}"
# A failed check exits 1; that is a result, not a unit failure.
SuccessExitStatus=1
TimeoutStartSec=90
EOF

cat > "${UNIT_DIR}/${UNIT}.timer" <<EOF
[Unit]
Description=Lego-Arbitrage uptime check every 2 minutes (${PROJECT})

[Timer]
OnBootSec=2min
OnCalendar=*-*-* *:0/2:20
AccuracySec=5s
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
