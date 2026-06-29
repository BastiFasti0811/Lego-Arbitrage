#!/usr/bin/env bash
#
# Nightly PostgreSQL backup for the LEGO-Arbitrage production stack.
# Dumps the running postgres container via `docker compose exec`, gzips the
# result to a temp file, verifies it (gzip integrity + pg_dump completion
# marker), atomically renames it into place, rotates old archives and
# (optionally) copies it off-site.
#
# Usage (from anywhere):   scripts/backup-db.sh
# Cron (daily 03:30):
#   30 3 * * * cd /opt/lego-arbitrage && PATH=/usr/local/bin:/usr/bin:/bin bash scripts/backup-db.sh >> /var/log/lego-backup.log 2>&1
#
# Honoured variables (read from .env.prod without shell-evaluating it, or from
# the real environment which takes precedence):
#   DATA_ROOT      base data dir; backups default to ${DATA_ROOT}/backups
#   BACKUP_DIR     override the backup destination directory
#   BACKUP_KEEP    how many dumps to retain (default 14)
#   BACKUP_REMOTE  optional rclone remote/path for an off-site copy
#   POSTGRES_DB    database name (used only for the backup filename)

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
BACKUP_REMOTE="${BACKUP_REMOTE:-$(env_get BACKUP_REMOTE)}"

if [[ -z "${BACKUP_DIR}" ]]; then
  if [[ -z "${DATA_ROOT}" ]]; then
    echo "Neither BACKUP_DIR nor DATA_ROOT is set (checked env and ${ENV_FILE})" >&2
    exit 1
  fi
  BACKUP_DIR="${DATA_ROOT}/backups"
fi

mkdir -p "${BACKUP_DIR}"

TS="$(date +%Y%m%d-%H%M%S)"
DEST_FILE="${BACKUP_DIR}/lego_${POSTGRES_DB}_${TS}.sql.gz"
PART_FILE="${DEST_FILE}.part"

# Remove the in-progress file on ANY abnormal exit so a failed/partial dump
# never lingers (it would otherwise count toward the rotation window).
cleanup() { rm -f "${PART_FILE}"; }
trap cleanup EXIT

echo "[$(date -Is)] Dumping ${POSTGRES_DB} -> ${DEST_FILE}"

# pg_dump inside the container; POSTGRES_USER/DB/PASSWORD resolve from the
# container env (compose), so this script never needs the credentials.
# --clean --if-exists makes the dump safe to restore into an existing database.
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' \
  | gzip -c > "${PART_FILE}"

# Verify 1: valid gzip container.
if ! gzip -t "${PART_FILE}" 2>/dev/null; then
  echo "Backup verification FAILED (corrupt gzip): ${PART_FILE}" >&2
  exit 1
fi

# Verify 2: pg_dump completed (plain-format dumps end with this marker). This
# catches a truncated dump that still happens to be a valid gzip member.
if ! gunzip -c "${PART_FILE}" | tail -c 200 | grep -q "PostgreSQL database dump complete"; then
  echo "Backup verification FAILED (incomplete dump — missing completion marker): ${PART_FILE}" >&2
  exit 1
fi

SIZE_BYTES="$(wc -c < "${PART_FILE}")"
echo "[$(date -Is)] Dump verified (${SIZE_BYTES} bytes)"

# Atomically publish the verified backup.
mv "${PART_FILE}" "${DEST_FILE}"
trap - EXIT

# Rotation: keep the newest BACKUP_KEEP archives, delete the rest.
mapfile -t OLD < <(ls -1t "${BACKUP_DIR}"/lego_*.sql.gz 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))")
if [[ "${#OLD[@]}" -gt 0 ]]; then
  echo "Pruning ${#OLD[@]} old backup(s) (keeping ${BACKUP_KEEP})"
  rm -f "${OLD[@]}"
fi

# Optional off-site copy. The local block volume is NOT in Hetzner backups, so
# this is the real disaster-recovery step. A missing/failed rclone must NOT mark
# an otherwise-good local backup as failed — warn instead.
if [[ -n "${BACKUP_REMOTE}" ]]; then
  if command -v rclone >/dev/null 2>&1; then
    echo "[$(date -Is)] Copying off-site -> ${BACKUP_REMOTE}"
    if ! rclone copy "${DEST_FILE}" "${BACKUP_REMOTE}"; then
      echo "WARNING: off-site copy to ${BACKUP_REMOTE} failed (local backup is intact)" >&2
    fi
  else
    echo "WARNING: BACKUP_REMOTE set but rclone not found on PATH — skipping off-site copy" >&2
  fi
fi

echo "[$(date -Is)] Backup finished successfully: ${DEST_FILE}"
