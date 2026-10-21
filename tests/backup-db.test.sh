#!/usr/bin/env bash
#
# Tests for scripts/backup-db.sh.
#
# Runs the real backup script with `docker`, `curl` and `rclone` stubs on PATH
# (rsync and tar are the real tools, against sandbox directories). Checks the
# photo archive, rotation, the off-site mirror in both forms, the alarm on
# every failure path and that the bot token never reaches argv or the log.
#
# Usage: bash tests/backup-db.test.sh

set -Eeuo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${TESTS_DIR}/.." && pwd)"
SCRIPT="${REPO_DIR}/scripts/backup-db.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

FAILURES=0
CURRENT_TEST=""
FAKE_TOKEN="123456:FAKE-token-for-tests"

fail() {
  echo "FAIL [${CURRENT_TEST}]: $1" >&2
  FAILURES=$((FAILURES + 1))
}

make_sandbox() {
  CURRENT_TEST="$1"
  SANDBOX="${TMP_ROOT}/$1"
  mkdir -p "${SANDBOX}/bin" "${SANDBOX}/data/media/inventory_photos/7" "${SANDBOX}/config"
  printf 'jpeg-bytes' > "${SANDBOX}/data/media/inventory_photos/7/abc.jpg"
  printf 'png-bytes' > "${SANDBOX}/data/media/inventory_photos/7/def.png"
  printf 'POSTGRES_DB=lego_arbitrage\nDATA_ROOT=%s\n' "${SANDBOX}/data" > "${SANDBOX}/env.prod"
  printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=4711\n' "${FAKE_TOKEN}" > "${SANDBOX}/config/alert.env"
  export TG_LOG="${SANDBOX}/telegram.log" CURL_ARGS_LOG="${SANDBOX}/curl-args.log" RCLONE_LOG="${SANDBOX}/rclone.log"
  : > "${TG_LOG}"; : > "${CURL_ARGS_LOG}"; : > "${RCLONE_LOG}"
  export DUMP_MODE=ok RCLONE_FAIL=0
  unset BACKUP_REMOTE BACKUP_KEEP BACKUP_MEDIA_KEEP BACKUP_MEDIA BACKUP_DIR BACKUP_MEDIA_DIR DATA_ROOT POSTGRES_DB

  cat > "${SANDBOX}/bin/docker" <<'STUB'
#!/usr/bin/env bash
# Only `docker compose ... exec -T postgres sh -c 'pg_dump ...'` is expected.
case "${DUMP_MODE}" in
  fail) echo "Error: service postgres is not running" >&2; exit 1 ;;
  truncated) printf -- '--\n-- PostgreSQL database dump\n--\nCOPY public.offers (id) FROM stdin;\n1\n' ;;
  *) printf -- '--\n-- PostgreSQL database dump\n--\nCOPY public.offers (id) FROM stdin;\n1\n2\n\\.\n\n--\n-- PostgreSQL database dump complete\n--\n' ;;
esac
STUB

  cat > "${SANDBOX}/bin/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${CURL_ARGS_LOG}"
{ printf 'CONFIG: %s\n' "$(cat)"; printf 'ARGS: %s\n' "$*"; } >> "${TG_LOG}"
exit 0
STUB

  cat > "${SANDBOX}/bin/rclone" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${RCLONE_LOG}"
[[ "${RCLONE_FAIL}" == "1" ]] && exit 1
exit 0
STUB
  chmod +x "${SANDBOX}/bin/docker" "${SANDBOX}/bin/curl" "${SANDBOX}/bin/rclone"
}

# Runs the backup once with extra VAR=value arguments; sets RC.
run_backup() {
  set +e
  env PATH="${SANDBOX}/bin:${PATH}" \
    ENV_FILE="${SANDBOX}/env.prod" \
    ALERT_CONFIG="${SANDBOX}/config/alert.env" \
    TELEGRAM_API_BASE="http://telegram.invalid" \
    "$@" \
    bash "${SCRIPT}" > "${SANDBOX}/out.log" 2>&1
  RC=$?
  set -e
}

BK() { echo "${SANDBOX}/data/backups"; }
tg_count() { grep -c '^CONFIG:' "${TG_LOG}" || true; }
count_files() { compgen -G "$1" | wc -l; }
expect_rc() { [[ "${RC}" == "$1" ]] || fail "expected exit $1, got ${RC}: $(tail -n2 "${SANDBOX}/out.log" | tr '\n' ' ')"; }
expect_tg() { [[ "$(tg_count)" == "$1" ]] || fail "expected $1 Telegram message(s), got $(tg_count)"; }

# ── 1. Success without remote: dump + photo archive, warning, no alarm ──
make_sandbox ok-no-remote
run_backup
expect_rc 0
expect_tg 0
[[ "$(count_files "$(BK)/lego_lego_arbitrage_*.sql.gz")" == "1" ]] || fail "one dump expected"
[[ "$(count_files "$(BK)/lego_media_*.tar")" == "1" ]] || fail "one photo archive expected"
[[ "$(count_files "$(BK)/*.part")" == "0" ]] || fail "no .part file may remain"
grep -q 'WARNING: BACKUP_REMOTE is empty' "${SANDBOX}/out.log" || fail "empty remote should warn"
media="$(compgen -G "$(BK)/lego_media_*.tar")"
tar -tf "${media}" | grep -qx 'media/inventory_photos/7/abc.jpg' || fail "archive should hold media/inventory_photos/7/abc.jpg"
[[ "$(stat -c %a "${media}")" == "600" ]] || fail "photo archive should be mode 600, is $(stat -c %a "${media}")"
dump="$(compgen -G "$(BK)/lego_lego_arbitrage_*.sql.gz")"
[[ "$(stat -c %a "${dump}")" == "600" ]] || fail "dump should be mode 600"

# ── 2. BACKUP_MEDIA=0 skips the photo archive ──
make_sandbox media-off
run_backup BACKUP_MEDIA=0
expect_rc 0
[[ "$(count_files "$(BK)/lego_media_*.tar")" == "0" ]] || fail "no photo archive expected"

# ── 3. Rotation keeps BACKUP_KEEP dumps and BACKUP_MEDIA_KEEP archives ──
make_sandbox rotation
mkdir -p "$(BK)"
for d in 20260101 20260102 20260103; do
  touch -d "${d}" "$(BK)/lego_lego_arbitrage_${d}-033000.sql.gz" "$(BK)/lego_media_${d}-033000.tar"
done
touch "$(BK)/cron.log"
run_backup BACKUP_KEEP=2 BACKUP_MEDIA_KEEP=1
expect_rc 0
[[ "$(count_files "$(BK)/lego_lego_arbitrage_*.sql.gz")" == "2" ]] || fail "2 dumps should remain"
[[ -f "$(BK)/lego_lego_arbitrage_20260103-033000.sql.gz" ]] || fail "newest old dump should remain"
[[ "$(count_files "$(BK)/lego_media_*.tar")" == "1" ]] || fail "1 photo archive should remain"
[[ -f "$(BK)/cron.log" ]] || fail "rotation must not touch cron.log"

# ── 4. pg_dump fails: exit 1, alarm names the step, nothing half-written ──
make_sandbox dump-fails
export DUMP_MODE=fail
run_backup
expect_rc 1
expect_tg 1
grep -q "🔴 Lego-Backup.*db-dump" "${TG_LOG}" || fail "alarm should name step db-dump"
[[ "$(count_files "$(BK)/lego_*")" == "0" ]] || fail "no archive may be left behind"

# ── 5. Truncated dump fails verification ──
make_sandbox dump-truncated
export DUMP_MODE=truncated
run_backup
expect_rc 1
expect_tg 1
grep -q "db-verify" "${TG_LOG}" || fail "alarm should name step db-verify"
[[ "$(count_files "$(BK)/lego_*")" == "0" ]] || fail "truncated dump must not be published"

# ── 6. Missing photo directory fails with alarm ──
make_sandbox media-missing
rm -rf "${SANDBOX}/data/media"
run_backup
expect_rc 1
expect_tg 1
grep -q "media-archive" "${TG_LOG}" || fail "alarm should name step media-archive"

# ── 7. Missing env file fails with alarm ──
make_sandbox no-env
rm -f "${SANDBOX}/env.prod"
run_backup
expect_rc 1
expect_tg 1
grep -q "config" "${TG_LOG}" || fail "alarm should name step config"

# ── 8. rclone remote: sync with include filters for both archive kinds ──
make_sandbox rclone-ok
run_backup BACKUP_REMOTE="rclone:crypt-box:lego"
expect_rc 0
expect_tg 0
grep -q "^sync $(BK) crypt-box:lego " "${RCLONE_LOG}" || fail "rclone sync to crypt-box:lego expected, got: $(cat "${RCLONE_LOG}")"
grep -q -- "--include /lego_lego_arbitrage_\*.sql.gz --include /lego_media_\*.tar" "${RCLONE_LOG}" || fail "rclone should filter to the archives"

# ── 9. Bare remote (older form) still uses rclone ──
make_sandbox rclone-bare
run_backup BACKUP_REMOTE="box:lego"
expect_rc 0
grep -q "^sync $(BK) box:lego " "${RCLONE_LOG}" || fail "bare remote should go to rclone"

# ── 10. rclone fails: exit 2, local backup kept, orange alarm ──
make_sandbox rclone-fails
export RCLONE_FAIL=1
run_backup BACKUP_REMOTE="rclone:box:lego"
expect_rc 2
expect_tg 1
grep -q "🟠 Lego-Backup.*Off-site-Kopie nach rclone:box:lego fehlgeschlagen" "${TG_LOG}" || fail "orange off-site alarm expected"
[[ "$(count_files "$(BK)/lego_lego_arbitrage_*.sql.gz")" == "1" ]] || fail "local dump must stay"

# ── 11. rsync remote mirrors the retained archives only ──
make_sandbox rsync-ok
mkdir -p "${SANDBOX}/remote"
touch "${SANDBOX}/remote/lego_lego_arbitrage_20200101-000000.sql.gz" "${SANDBOX}/remote/foreign.txt"
run_backup BACKUP_REMOTE="rsync:${SANDBOX}/remote"
expect_rc 0
[[ "$(count_files "${SANDBOX}/remote/lego_lego_arbitrage_*.sql.gz")" == "1" ]] || fail "remote should mirror exactly one dump"
[[ ! -e "${SANDBOX}/remote/lego_lego_arbitrage_20200101-000000.sql.gz" ]] || fail "rotated dump should be removed remotely"
[[ "$(count_files "${SANDBOX}/remote/lego_media_*.tar")" == "1" ]] || fail "remote should hold the photo archive"
[[ ! -e "${SANDBOX}/remote/.backup.lock" ]] || fail "lock file must not be mirrored"
[[ -e "${SANDBOX}/remote/foreign.txt" ]] || fail "foreign remote files must not be deleted"

# ── 12. rsync target unreachable: exit 2 + alarm ──
make_sandbox rsync-fails
run_backup BACKUP_REMOTE="rsync:${SANDBOX}/does/not/exist/below"
expect_rc 2
expect_tg 1

# ── 13. Token travels via stdin, never in argv or output ──
make_sandbox token-hidden
export DUMP_MODE=fail
run_backup
grep -q "bot${FAKE_TOKEN}/sendMessage" "${TG_LOG}" || fail "token URL should be passed on stdin"
grep -qF "${FAKE_TOKEN}" "${CURL_ARGS_LOG}" && fail "token must not appear in curl's argv"
grep -qF "${FAKE_TOKEN}" "${SANDBOX}/out.log" && fail "token must not appear in the output"

# ── 14. No Telegram config: failure is logged, exit code unchanged ──
make_sandbox no-telegram
: > "${SANDBOX}/config/alert.env"
export DUMP_MODE=fail
run_backup
expect_rc 1
expect_tg 0
grep -q 'ALARM NOT SENT' "${SANDBOX}/out.log" || fail "missing Telegram config should be logged"

# ── 15. Invalid numbers abort before any work ──
make_sandbox bad-keep
run_backup BACKUP_KEEP=zero
expect_rc 1
[[ "$(count_files "$(BK)/lego_*")" == "0" ]] || fail "nothing may be written with a bad setting"

if (( FAILURES > 0 )); then
  echo "${FAILURES} backup-db test assertion(s) failed" >&2
  exit 1
fi
echo "backup-db tests passed"
