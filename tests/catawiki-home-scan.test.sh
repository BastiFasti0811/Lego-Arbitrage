#!/usr/bin/env bash
#
# Tests for scripts/catawiki-home-scan.sh and scripts/install-catawiki-home-scan.sh.
#
# Runs the real scripts with `docker` and `systemctl` stubs on PATH. The docker
# stub answers `ps` from per-test variables, logs every `exec` call and copies
# the --env-file it was given, so the tests check that the token reaches the
# container only through that file (never argv) and that the file is removed.
#
# Usage: bash tests/catawiki-home-scan.test.sh

set -Eeuo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${TESTS_DIR}/.." && pwd)"
RUNNER="${REPO_DIR}/scripts/catawiki-home-scan.sh"
INSTALLER="${REPO_DIR}/scripts/install-catawiki-home-scan.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

FAILURES=0
CURRENT_TEST=""
TOKEN="tok-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"

fail() {
  echo "FAIL [${CURRENT_TEST}]: $1" >&2
  FAILURES=$((FAILURES + 1))
}

make_sandbox() {
  CURRENT_TEST="$1"
  SANDBOX="${TMP_ROOT}/$1"
  mkdir -p "${SANDBOX}/bin" "${SANDBOX}/config/lego-catawiki-scan" "${SANDBOX}/run"
  export XDG_CONFIG_HOME="${SANDBOX}/config" XDG_RUNTIME_DIR="${SANDBOX}/run"
  export DOCKER_LOG="${SANDBOX}/docker.log" ENV_COPY="${SANDBOX}/env-copy" SYSTEMCTL_LOG="${SANDBOX}/systemctl.log"
  : > "${DOCKER_LOG}"
  : > "${SYSTEMCTL_LOG}"
  export API_RUNNING=1 PG_RUNNING=1 EXEC_STATUS=0 DB_TOKEN="${TOKEN}"
  export DB_URLS="https://www.catawiki.com/de/c/375-lego" DB_FREQUENCY="daily"
  CONFIG="${SANDBOX}/config/lego-catawiki-scan/lego-arbitrage.env"
  unset COMPOSE_PROJECT CATAWIKI_SCAN_CONFIG LOCK_FILE

  cat > "${SANDBOX}/bin/docker" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  ps)
    svc="$(printf '%s\n' "$@" | sed -n 's/^label=com.docker.compose.service=//p')"
    [[ "${svc}" == "api" && "${API_RUNNING}" == "1" ]] && echo "id-api"
    [[ "${svc}" == "postgres" && "${PG_RUNNING}" == "1" ]] && echo "id-pg"
    exit 0 ;;
  exec)
    printf '%s\n' "$*" >> "${DOCKER_LOG}"
    if [[ "$2" == "--env-file" ]]; then
      cp "$3" "${ENV_COPY}"
      stat -c '%a' "$3" > "${ENV_COPY}.mode"
      exit "${EXEC_STATUS}"
    fi
    # psql read of app_settings
    case "$*" in
      *"key = 'remote_scan_token'"*) printf '%s\n' "${DB_TOKEN}" ;;
      *"key = 'catawiki_scan_urls'"*) printf '%s\n' "${DB_URLS}" ;;
      *"key = 'catawiki_scan_frequency'"*) printf '%s\n' "${DB_FREQUENCY}" ;;
    esac ;;
esac
STUB
  cat > "${SANDBOX}/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SYSTEMCTL_LOG}"
STUB
  chmod +x "${SANDBOX}/bin/docker" "${SANDBOX}/bin/systemctl"
  export PATH="${SANDBOX}/bin:${ORIGINAL_PATH}"
}

write_config() {
  printf 'COMPOSE_PROJECT=lego-arbitrage\nLEGO_REMOTE_SCAN_TOKEN=%s\n' "${TOKEN}" > "${CONFIG}"
  chmod 600 "${CONFIG}"
}

run_runner() {
  set +e
  OUTPUT="$(bash "${RUNNER}" "$@" 2>&1)"
  STATUS=$?
  set -e
}

run_installer() {
  set +e
  OUTPUT="$(bash "${INSTALLER}" "$@" 2>&1)"
  STATUS=$?
  set -e
}

ORIGINAL_PATH="${PATH}"

# ── Runner ───────────────────────────────────────────────

make_sandbox runner_happy_path
write_config
run_runner --dry-run
[[ "${STATUS}" == "0" ]] || fail "expected exit 0, got ${STATUS}: ${OUTPUT}"
grep -q -- "--env-file .* id-api python -m app.tools.catawiki_home_scan --dry-run$" "${DOCKER_LOG}" \
  || fail "docker exec call wrong: $(cat "${DOCKER_LOG}")"
grep -q "${TOKEN}" "${DOCKER_LOG}" && fail "token leaked into docker argv"
grep -qx "LEGO_REMOTE_SCAN_TOKEN=${TOKEN}" "${ENV_COPY}" || fail "token missing in env file"
grep -qx "LEGO_API_URL=http://127.0.0.1:8000" "${ENV_COPY}" || fail "default API URL missing"
grep -q "COMPOSE_PROJECT" "${ENV_COPY}" && fail "env file should carry only the two tool keys"
[[ "$(cat "${ENV_COPY}.mode")" == "600" ]] || fail "env file mode $(cat "${ENV_COPY}.mode"), expected 600"
[[ -z "$(ls -A "${SANDBOX}/run" | grep -v '\.lock$' || true)" ]] || fail "temporary env file not removed"

make_sandbox runner_custom_api_url
write_config
printf 'LEGO_API_URL=http://127.0.0.1:9000\r\n' >> "${CONFIG}"
run_runner
grep -qx "LEGO_API_URL=http://127.0.0.1:9000" "${ENV_COPY}" || fail "LEGO_API_URL from config (CRLF) not used"

make_sandbox runner_tool_error_is_exit_1
write_config
export EXEC_STATUS=3
run_runner
[[ "${STATUS}" == "1" ]] || fail "expected exit 1 on tool error, got ${STATUS}"

make_sandbox runner_without_config
run_runner
[[ "${STATUS}" == "2" ]] || fail "expected exit 2 without config, got ${STATUS}"
[[ "${OUTPUT}" == *"install-catawiki-home-scan.sh"* ]] || fail "missing install hint: ${OUTPUT}"

make_sandbox runner_without_token
printf 'COMPOSE_PROJECT=lego-arbitrage\n' > "${CONFIG}"
run_runner
[[ "${STATUS}" == "2" ]] || fail "expected exit 2 without token, got ${STATUS}"
[[ -s "${DOCKER_LOG}" ]] && fail "docker exec must not run without token"

make_sandbox runner_without_api_container
write_config
export API_RUNNING=0
run_runner
[[ "${STATUS}" == "2" ]] || fail "expected exit 2 without api container, got ${STATUS}"
[[ "${OUTPUT}" == *"No running api container"* ]] || fail "unexpected output: ${OUTPUT}"

make_sandbox runner_bad_project_name
write_config
printf 'COMPOSE_PROJECT=../evil\n' >> "${CONFIG}"
run_runner
[[ "${STATUS}" == "2" ]] || fail "expected exit 2 for invalid project, got ${STATUS}"

make_sandbox runner_skips_while_locked
write_config
exec 8>"${XDG_RUNTIME_DIR}/lego-catawiki-scan-lego-arbitrage.lock"
flock -n 8
run_runner
exec 8>&-
[[ "${STATUS}" == "75" ]] || fail "expected exit 75 while another run holds the lock, got ${STATUS}"
[[ -s "${DOCKER_LOG}" ]] && fail "docker exec must not run while locked"

# ── Installer ────────────────────────────────────────────

make_sandbox installer_imports_token_and_writes_units
run_installer --import-token --no-start
[[ "${STATUS}" == "0" ]] || fail "installer failed (${STATUS}): ${OUTPUT}"
[[ "$(stat -c '%a' "${CONFIG}")" == "600" ]] || fail "config mode is not 600"
grep -qx "LEGO_REMOTE_SCAN_TOKEN=${TOKEN}" "${CONFIG}" || fail "token not imported"
grep -qx "LEGO_API_URL=http://127.0.0.1:8000" "${CONFIG}" || fail "default API URL not written"
[[ "${OUTPUT}" == *"${TOKEN}"* ]] && fail "installer printed the token"
[[ "${OUTPUT}" == *"WARNING"* ]] && fail "unexpected warning: ${OUTPUT}"
UNIT_DIR="${XDG_CONFIG_HOME}/systemd/user"
grep -q "ExecStart=.*scripts/catawiki-home-scan.sh\"$" "${UNIT_DIR}/lego-catawiki-scan-lego-arbitrage.service" \
  || fail "service ExecStart wrong"
grep -q "CATAWIKI_SCAN_CONFIG=${CONFIG}" "${UNIT_DIR}/lego-catawiki-scan-lego-arbitrage.service" \
  || fail "service does not pin the config file"
grep -qx "OnCalendar=\*-\*-\* \*:03/10:00" "${UNIT_DIR}/lego-catawiki-scan-lego-arbitrage.timer" \
  || fail "timer schedule wrong"
grep -q "enable" "${SYSTEMCTL_LOG}" && fail "--no-start must not enable the timer"

make_sandbox installer_keeps_existing_token_and_warns_on_mismatch
write_config
export DB_TOKEN="other-token-0123456789012345678901"
run_installer --no-start --interval 5
grep -qx "LEGO_REMOTE_SCAN_TOKEN=${TOKEN}" "${CONFIG}" || fail "existing token replaced"
[[ "${OUTPUT}" == *"differs from app_settings"* ]] || fail "mismatch not reported: ${OUTPUT}"
[[ "${OUTPUT}" == *"${DB_TOKEN}"* ]] && fail "installer printed the database token"
grep -qx "OnCalendar=\*-\*-\* \*:03/5:00" "${XDG_CONFIG_HOME}/systemd/user/lego-catawiki-scan-lego-arbitrage.timer" \
  || fail "--interval not applied"

make_sandbox installer_generates_token_and_notes_missing_setup
export DB_TOKEN="" DB_URLS="" DB_FREQUENCY="off"
run_installer
[[ "${STATUS}" == "0" ]] || fail "installer failed (${STATUS}): ${OUTPUT}"
token_len="$(sed -n 's/^LEGO_REMOTE_SCAN_TOKEN=//p' "${CONFIG}" | tr -d '\n' | wc -c)"
[[ "${token_len}" == "43" ]] || fail "generated token has ${token_len} characters, expected 43"
grep -q "^LEGO_REMOTE_SCAN_TOKEN=[A-Za-z0-9_-]*$" "${CONFIG}" || fail "generated token is not URL-safe"
[[ "${OUTPUT}" == *"no remote_scan_token yet"* ]] || fail "missing token warning: ${OUTPUT}"
[[ "${OUTPUT}" == *"catawiki_scan_urls is empty"* ]] || fail "missing URL note"
[[ "${OUTPUT}" == *"catawiki_scan_frequency is off"* ]] || fail "missing frequency note"
grep -q "enable --now lego-catawiki-scan-lego-arbitrage.timer" "${SYSTEMCTL_LOG}" || fail "timer not enabled"

make_sandbox installer_import_without_db_token_fails
export DB_TOKEN="short"
run_installer --import-token --no-start
[[ "${STATUS}" == "1" ]] || fail "expected exit 1 when the app has no usable token, got ${STATUS}"

make_sandbox installer_rejects_bad_interval
run_installer --interval 0
[[ "${STATUS}" == "2" ]] || fail "expected exit 2 for --interval 0, got ${STATUS}"
run_installer --interval abc
[[ "${STATUS}" == "2" ]] || fail "expected exit 2 for --interval abc, got ${STATUS}"

make_sandbox installer_uninstall_removes_units_keeps_config
run_installer --import-token --no-start
run_installer --uninstall
[[ "${STATUS}" == "0" ]] || fail "uninstall failed: ${OUTPUT}"
[[ -e "${XDG_CONFIG_HOME}/systemd/user/lego-catawiki-scan-lego-arbitrage.timer" ]] && fail "timer unit left behind"
[[ -s "${CONFIG}" ]] || fail "uninstall removed the config"

if (( FAILURES > 0 )); then
  echo "${FAILURES} failure(s)" >&2
  exit 1
fi
echo "All catawiki home scan script tests passed."
