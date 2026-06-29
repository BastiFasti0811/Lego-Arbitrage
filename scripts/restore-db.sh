#!/usr/bin/env bash
#
# Restore a LEGO-Arbitrage PostgreSQL backup created by backup-db.sh.
#
# Usage:   CONFIRM=yes scripts/restore-db.sh <path-to-backup.sql.gz>
#
# DESTRUCTIVE: the dump is created with --clean --if-exists, so restoring
# DROPs and recreates every object in the target database. You must confirm
# by setting CONFIRM=yes. If the database name embedded in the backup filename
# differs from the target database, the restore refuses unless ALLOW_DB_MISMATCH=yes.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

cd "${REPO_DIR}"

BACKUP_FILE="${1:-}"
if [[ -z "${BACKUP_FILE}" ]]; then
  echo "Usage: CONFIRM=yes $0 <path-to-backup.sql.gz>" >&2
  exit 2
fi
if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "Backup file not found: ${BACKUP_FILE}" >&2
  exit 2
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE} in ${REPO_DIR}" >&2
  exit 1
fi
if ! gzip -t "${BACKUP_FILE}" 2>/dev/null; then
  echo "Backup file is not a valid gzip archive: ${BACKUP_FILE}" >&2
  exit 1
fi

# Read target DB name from the env file without shell-evaluating it.
# tr -d '\r' tolerates CRLF env files (e.g. edited on Windows).
env_get() { sed -n "s/^$1=//p" "${ENV_FILE}" | tail -n1 | tr -d '\r'; }
TARGET_DB="${POSTGRES_DB:-$(env_get POSTGRES_DB)}"
TARGET_DB="${TARGET_DB:-lego_arbitrage}"

# Backups are named lego_<db>_<timestamp>.sql.gz — recover <db> for a sanity check.
BASE="$(basename "${BACKUP_FILE}")"
SOURCE_DB="${BASE#lego_}"
SOURCE_DB="${SOURCE_DB%_*.sql.gz}"

echo "Restore target database : ${TARGET_DB}"
echo "Backup source database  : ${SOURCE_DB}"
echo "Backup file             : ${BACKUP_FILE}"

if [[ "${SOURCE_DB}" != "${TARGET_DB}" && "${ALLOW_DB_MISMATCH:-}" != "yes" ]]; then
  echo "Refusing: backup is from DB '${SOURCE_DB}' but target is '${TARGET_DB}'." >&2
  echo "If this is intentional, re-run with ALLOW_DB_MISMATCH=yes." >&2
  exit 3
fi
if [[ "${CONFIRM:-}" != "yes" ]]; then
  echo "Refusing to restore without CONFIRM=yes (this overwrites database '${TARGET_DB}')." >&2
  echo "Re-run as: CONFIRM=yes $0 ${BACKUP_FILE}" >&2
  exit 3
fi

echo "[$(date -Is)] Restoring ${BACKUP_FILE} into '${TARGET_DB}'"

gunzip -c "${BACKUP_FILE}" \
  | docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
      sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

echo "[$(date -Is)] Restore finished successfully."
echo "Reminder: run 'alembic upgrade head' if the backup predates the current schema."
