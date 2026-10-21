#!/usr/bin/env bash
#
# Nightly backup for the LEGO-Arbitrage production stack:
#   1. PostgreSQL: dumps the running postgres container via `docker compose
#      exec`, gzips it to a temp file, verifies it (gzip integrity + pg_dump
#      completion marker) and atomically renames it into place.
#   2. Inventory photos: archives ${DATA_ROOT}/media into an uncompressed tar
#      (photos are JPEG/PNG already; an uncompressed tar also lets a
#      deduplicating backup such as restic store only the new photos).
#   3. Rotation of both archive kinds.
#   4. Optional off-site mirror of the archives (BACKUP_REMOTE).
# Any failure sends a Telegram alarm (scripts/lib/telegram.sh, same config file
# as the uptime check) and exits non-zero.
#
# Usage (from anywhere):   scripts/backup-db.sh
# On victus the job runs from the NixOS system cron (services.cron), daily 03:30:
#   30 3 * * * sebastian PATH=/run/current-system/sw/bin cd /srv/lego-arbitrage && bash scripts/backup-db.sh >> /mnt/HC_Volume_105179687/lego-arbitrage/backups/cron.log 2>&1
#
# Exit codes: 0 ok (also when BACKUP_REMOTE is empty: warning only),
#             1 local backup failed, 2 local backup ok but off-site copy failed.
#
# Honoured variables (read from .env.prod without shell-evaluating it, or from
# the real environment which takes precedence):
#   DATA_ROOT          base data dir; backups default to ${DATA_ROOT}/backups
#   BACKUP_DIR         override the backup destination directory
#   BACKUP_KEEP        how many database dumps to retain (default 14)
#   BACKUP_MEDIA       1 = also archive the photos (default), 0 = skip
#   BACKUP_MEDIA_DIR   photo directory (default ${DATA_ROOT}/media)
#   BACKUP_MEDIA_KEEP  how many photo archives to retain (default 7)
#   BACKUP_REMOTE      off-site target, mirrored after every run:
#                        rclone:<remote>:<path>     rclone sync (e.g. a crypt remote)
#                        rsync:[user@]host:/path    rsync over ssh
#                        <remote>:<path>            same as rclone: (older form)
#                      Empty = no off-site copy from this script (warning only).
#   POSTGRES_DB        database name (used only for the backup filename)
# Alarm settings (environment only):
#   ALERT_CONFIG / ALERT_PROJECT / TELEGRAM_*   see scripts/lib/telegram.sh
#   BACKUP_LOCK_WAIT   seconds to wait for a concurrent backup (default 900)

set -Eeuo pipefail

# Dumps contain app_settings (bot tokens, API keys): archives are owner-only.
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

# shellcheck source=SCRIPTDIR/lib/telegram.sh
source "${SCRIPT_DIR}/lib/telegram.sh"

HOST="$(hostname 2>/dev/null || echo unknown-host)"
STEP="start"
PART_FILES=()
OFFSITE_FAILED=0

log() { echo "[$(date -Is)] $*"; }

# Every abnormal exit (set -e, explicit exit 1) ends here: remove half-written
# archives so they never count toward rotation, then raise the alarm.
on_exit() {
  local rc=$?
  if (( ${#PART_FILES[@]} > 0 )); then
    rm -f "${PART_FILES[@]}"
  fi
  if (( rc == 0 )); then
    return 0
  fi
  if (( rc == 2 && OFFSITE_FAILED == 1 )); then
    # Strip any "user:secret@" so credentials embedded in the target never reach the chat.
    local remote_shown
    remote_shown="$(printf '%s' "${BACKUP_REMOTE:-?}" | sed -E 's#[^ /@:]+:[^ /@]+@#***@#')"
    tg_send "🟠 Lego-Backup (${HOST}): lokale Sicherung ok, Off-site-Kopie nach ${remote_shown} fehlgeschlagen. Log: ${BACKUP_DIR:-?}/cron.log" || true
  else
    tg_send "🔴 Lego-Backup (${HOST}) fehlgeschlagen im Schritt '${STEP}' (Exit ${rc}). Kein neues Backup von heute. Log: ${BACKUP_DIR:-${DATA_ROOT:-?}/backups}/cron.log" || true
  fi
  return "${rc}"
}
trap on_exit EXIT

die() {
  echo "ERROR (${STEP}): $*" >&2
  exit 1
}

cd "${REPO_DIR}"

ALERT_PROJECT="${ALERT_PROJECT:-${COMPOSE_PROJECT_NAME:-lego-arbitrage}}"
tg_load_config

STEP="config"
[[ -f "${ENV_FILE}" ]] || die "Missing ${ENV_FILE} in ${REPO_DIR}"

# Read a single KEY=value from the env file WITHOUT sourcing it, so values with
# shell metacharacters (e.g. a strong DB password) can never break the script
# or execute code. Matches how `docker compose --env-file` treats the file.
# `tr -d '\r'` tolerates CRLF env files (e.g. edited on Windows) — a trailing
# carriage return would otherwise corrupt every value (paths, counts, db name).
env_get() {
  sed -n "s/^$1=//p" "${ENV_FILE}" | tail -n1 | tr -d '\r'
}

# Real environment wins over the file (lets cron / operators override).
POSTGRES_DB="${POSTGRES_DB:-$(env_get POSTGRES_DB)}"
POSTGRES_DB="${POSTGRES_DB:-lego_arbitrage}"
DATA_ROOT="${DATA_ROOT:-$(env_get DATA_ROOT)}"
BACKUP_DIR="${BACKUP_DIR:-$(env_get BACKUP_DIR)}"
BACKUP_KEEP="${BACKUP_KEEP:-$(env_get BACKUP_KEEP)}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
BACKUP_MEDIA="${BACKUP_MEDIA:-$(env_get BACKUP_MEDIA)}"
BACKUP_MEDIA="${BACKUP_MEDIA:-1}"
BACKUP_MEDIA_DIR="${BACKUP_MEDIA_DIR:-$(env_get BACKUP_MEDIA_DIR)}"
BACKUP_MEDIA_KEEP="${BACKUP_MEDIA_KEEP:-$(env_get BACKUP_MEDIA_KEEP)}"
BACKUP_MEDIA_KEEP="${BACKUP_MEDIA_KEEP:-7}"
BACKUP_REMOTE="${BACKUP_REMOTE:-$(env_get BACKUP_REMOTE)}"
BACKUP_LOCK_WAIT="${BACKUP_LOCK_WAIT:-900}"

for num in BACKUP_KEEP BACKUP_MEDIA_KEEP BACKUP_LOCK_WAIT; do
  if ! [[ "${!num}" =~ ^[0-9]+$ ]] || (( ${!num} < 1 )); then
    die "${num} must be a positive integer, got '${!num}'"
  fi
done
[[ "${BACKUP_MEDIA}" =~ ^[01]$ ]] || die "BACKUP_MEDIA must be 0 or 1, got '${BACKUP_MEDIA}'"

if [[ -z "${BACKUP_DIR}" ]]; then
  [[ -n "${DATA_ROOT}" ]] || die "Neither BACKUP_DIR nor DATA_ROOT is set (checked env and ${ENV_FILE})"
  BACKUP_DIR="${DATA_ROOT}/backups"
fi
if [[ "${BACKUP_MEDIA}" == "1" && -z "${BACKUP_MEDIA_DIR}" ]]; then
  [[ -n "${DATA_ROOT}" ]] || die "BACKUP_MEDIA=1 but neither BACKUP_MEDIA_DIR nor DATA_ROOT is set"
  BACKUP_MEDIA_DIR="${DATA_ROOT}/media"
fi

mkdir -p "${BACKUP_DIR}"

# A manual run (e.g. before a migration) and the nightly cron must not
# interleave their rotation and off-site mirror.
STEP="lock"
exec 8>"${BACKUP_DIR}/.backup.lock"
flock -w "${BACKUP_LOCK_WAIT}" 8 || die "another backup still running after ${BACKUP_LOCK_WAIT}s"

TS="$(date +%Y%m%d-%H%M%S)"

# ── 1. Database ──────────────────────────────────────────
STEP="db-dump"
DEST_FILE="${BACKUP_DIR}/lego_${POSTGRES_DB}_${TS}.sql.gz"
PART_FILE="${DEST_FILE}.part"
PART_FILES+=("${PART_FILE}")

log "Dumping ${POSTGRES_DB} -> ${DEST_FILE}"

# pg_dump inside the container; POSTGRES_USER/DB/PASSWORD resolve from the
# container env (compose), so this script never needs the credentials.
# --clean --if-exists makes the dump safe to restore into an existing database.
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' \
  | gzip -c > "${PART_FILE}"

STEP="db-verify"
# Verify 1: valid gzip container.
gzip -t "${PART_FILE}" 2>/dev/null || die "Backup verification FAILED (corrupt gzip): ${PART_FILE}"

# Verify 2: pg_dump completed (plain-format dumps end with this marker). This
# catches a truncated dump that still happens to be a valid gzip member.
if ! gunzip -c "${PART_FILE}" | tail -c 200 | grep -q "PostgreSQL database dump complete"; then
  die "Backup verification FAILED (incomplete dump — missing completion marker): ${PART_FILE}"
fi

log "Dump verified ($(wc -c < "${PART_FILE}") bytes)"
mv "${PART_FILE}" "${DEST_FILE}"

# ── 2. Inventory photos ─────────────────────────────────
MEDIA_FILE=""
if [[ "${BACKUP_MEDIA}" == "1" ]]; then
  STEP="media-archive"
  [[ -d "${BACKUP_MEDIA_DIR}" ]] || die "Photo directory not found: ${BACKUP_MEDIA_DIR}"
  MEDIA_FILE="${BACKUP_DIR}/lego_media_${TS}.tar"
  MEDIA_PART="${MEDIA_FILE}.part"
  PART_FILES+=("${MEDIA_PART}")
  source_count="$(find "${BACKUP_MEDIA_DIR}" -type f | wc -l)"
  log "Archiving photos ${BACKUP_MEDIA_DIR} (${source_count} files) -> ${MEDIA_FILE}"

  # Entries are stored as media/<...> relative to the parent directory, which
  # is what restore-probe.sh and a restore expect. GNU tar exits 1 when a file
  # changed while it was read (a photo uploaded mid-run): the archive is still
  # complete for everything else, so that is a warning, not a failure.
  tar_rc=0
  tar -C "$(dirname -- "${BACKUP_MEDIA_DIR}")" -cf "${MEDIA_PART}" "$(basename -- "${BACKUP_MEDIA_DIR}")" || tar_rc=$?
  if (( tar_rc == 1 )); then
    echo "WARNING: some photos changed while they were archived (tar exit 1)" >&2
  elif (( tar_rc != 0 )); then
    die "tar failed with exit ${tar_rc} (unreadable photo or full disk?)"
  fi

  STEP="media-verify"
  listing="$(tar -tf "${MEDIA_PART}" 2>/dev/null)" \
    || die "Photo archive verification FAILED (unreadable tar): ${MEDIA_PART}"
  archived_count="$(grep -vc '/$' <<<"${listing}" || true)"
  if (( source_count > 0 && archived_count == 0 )); then
    die "Photo archive verification FAILED (0 of ${source_count} files archived)"
  fi
  log "Photo archive verified (${archived_count} files, $(wc -c < "${MEDIA_PART}") bytes)"
  mv "${MEDIA_PART}" "${MEDIA_FILE}"
fi
PART_FILES=()

# ── 3. Rotation ─────────────────────────────────────────
STEP="rotation"
# prune <keep> <glob>: keep the newest <keep> files matching <glob>.
prune() {
  local keep="$1" pattern="$2" old
  mapfile -t old < <(compgen -G "${pattern}" | xargs -r ls -1t 2>/dev/null | tail -n +"$((keep + 1))")
  if (( ${#old[@]} > 0 )); then
    log "Pruning ${#old[@]} old archive(s) matching $(basename -- "${pattern}") (keeping ${keep})"
    rm -f "${old[@]}"
  fi
}
prune "${BACKUP_KEEP}" "${BACKUP_DIR}/lego_${POSTGRES_DB}_*.sql.gz"
prune "${BACKUP_MEDIA_KEEP}" "${BACKUP_DIR}/lego_media_*.tar"

# ── 4. Off-site mirror ──────────────────────────────────
# The remote mirrors exactly the retained archives (dumps + photo archives),
# so its size stays bounded by the local rotation. Other files in BACKUP_DIR
# (cron.log, manual/, the lock) are not copied.
STEP="offsite"
offsite_mirror() {
  local target
  case "${BACKUP_REMOTE}" in
    rsync:*)
      target="${BACKUP_REMOTE#rsync:}"
      command -v rsync >/dev/null 2>&1 || { echo "rsync not found on PATH" >&2; return 1; }
      rsync -a --delete \
        --include="lego_${POSTGRES_DB}_*.sql.gz" --include='lego_media_*.tar' --exclude='*' \
        "${BACKUP_DIR}/" "${target%/}/"
      ;;
    *)
      target="${BACKUP_REMOTE#rclone:}"
      command -v rclone >/dev/null 2>&1 || { echo "rclone not found on PATH" >&2; return 1; }
      # With include filters, `rclone sync` only deletes remote files that
      # match them, so foreign files on the remote are left alone.
      rclone sync "${BACKUP_DIR}" "${target}" \
        --include "/lego_${POSTGRES_DB}_*.sql.gz" --include "/lego_media_*.tar"
      ;;
  esac
}

if [[ -z "${BACKUP_REMOTE}" ]]; then
  echo "WARNING: BACKUP_REMOTE is empty — this script made no off-site copy. Archives live only on ${BACKUP_DIR} (plus whatever host backup covers that path)." >&2
else
  log "Mirroring archives off-site -> ${BACKUP_REMOTE}"
  if ! offsite_mirror; then
    echo "ERROR: off-site mirror to ${BACKUP_REMOTE} failed (local backup is intact)" >&2
    OFFSITE_FAILED=1
    exit 2
  fi
  log "Off-site mirror ok"
fi

log "Backup finished successfully: ${DEST_FILE}${MEDIA_FILE:+ + ${MEDIA_FILE}}"
