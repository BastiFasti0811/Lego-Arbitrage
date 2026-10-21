# shellcheck shell=bash
#
# Telegram alarm helper for the ops scripts (backup-db.sh, restore-probe.sh).
# Source it, then call `tg_load_config` once and `tg_send "<text>"` per alarm.
#
# Token and chat id come from the same config file the uptime check uses
# (~/.config/lego-uptime/<project>.env, mode 0600, KEY=value lines that are
# read without sourcing), or from the real environment, which wins. The token
# is part of the URL; it is handed to curl on stdin (`-K -`) so it never shows
# up in argv, the process list or the logs.
#
# Variables:
#   ALERT_CONFIG        config file (default ~/.config/lego-uptime/${ALERT_PROJECT:-lego-arbitrage}.env)
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
#   TELEGRAM_API_BASE   default https://api.telegram.org (tests point it elsewhere)

tg_load_config() {
  local dir="${XDG_CONFIG_HOME:-${HOME}/.config}/lego-uptime"
  ALERT_CONFIG="${ALERT_CONFIG:-${dir}/${ALERT_PROJECT:-lego-arbitrage}.env}"
  TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-$(_tg_cfg_get TELEGRAM_BOT_TOKEN)}"
  TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-$(_tg_cfg_get TELEGRAM_CHAT_ID)}"
  TELEGRAM_API_BASE="${TELEGRAM_API_BASE:-https://api.telegram.org}"
}

_tg_cfg_get() {
  [[ -r "${ALERT_CONFIG}" ]] || return 0
  sed -n "s/^$1=//p" "${ALERT_CONFIG}" | tail -n1 | tr -d '\r'
}

# Returns 0 when Telegram accepted the message, 1 otherwise (never exits).
tg_send() {
  local text="$1"
  if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
    echo "[$(date -Is)] ALARM NOT SENT (no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID in ${ALERT_CONFIG:-env}): ${text}" >&2
    return 1
  fi
  if printf 'url = "%s/bot%s/sendMessage"\n' "${TELEGRAM_API_BASE}" "${TELEGRAM_BOT_TOKEN}" \
    | curl -sS -o /dev/null --fail --max-time 15 -K - \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${text}" \
        --data "disable_web_page_preview=true" 2>/dev/null; then
    echo "[$(date -Is)] alarm sent via Telegram"
    return 0
  fi
  echo "[$(date -Is)] ALARM NOT SENT (Telegram rejected the request): ${text}" >&2
  return 1
}
