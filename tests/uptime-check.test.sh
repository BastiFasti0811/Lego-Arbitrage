#!/usr/bin/env bash
#
# Tests for scripts/uptime-check.sh.
#
# Runs the real check script with `docker` and `curl` stubs on PATH. The
# stubs answer from per-test variables (health code/body, container states)
# and log every Telegram call, so the alarm logic (threshold, repeat, change,
# all-clear, retry after a failed send) is checked without containers or
# network.
#
# Usage: bash tests/uptime-check.test.sh

set -Eeuo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${TESTS_DIR}/.." && pwd)"
SCRIPT="${REPO_DIR}/scripts/uptime-check.sh"

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
  mkdir -p "${SANDBOX}/bin" "${SANDBOX}/state" "${SANDBOX}/config"
  export TG_LOG="${SANDBOX}/telegram.log" CURL_ARGS_LOG="${SANDBOX}/curl-args.log"
  : > "${TG_LOG}"
  : > "${CURL_ARGS_LOG}"
  printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=4711\n' "${FAKE_TOKEN}" > "${SANDBOX}/config/test.env"

  # Per-test knobs (exported for the stubs).
  export HEALTH_CODE=200
  export HEALTH_BODY='{"status": "healthy", "checks": {"database": "ok", "redis": "ok"}}'
  export TG_FAIL=0 DOCKER_DOWN=0 MISSING_SERVICES=""
  export STATE_api="running healthy" STATE_worker="running healthy" STATE_beat="running healthy"
  export STATE_frontend="running none" STATE_postgres="running healthy" STATE_redis="running healthy"

  cat > "${SANDBOX}/bin/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${CURL_ARGS_LOG}"
if [[ " $* " == *" -K - "* ]]; then
  # Telegram call: the URL (with the token) arrives on stdin.
  {
    printf 'CONFIG: %s\n' "$(cat)"
    printf 'ARGS: %s\n' "$*"
  } >> "${TG_LOG}"
  [[ "${TG_FAIL}" == "1" ]] && exit 22
  exit 0
fi
out=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  [[ "${args[i]}" == "-o" ]] && out="${args[i + 1]}"
done
[[ -n "${out}" ]] && printf '%s' "${HEALTH_BODY}" > "${out}"
if [[ "${HEALTH_CODE}" == "000" ]]; then
  printf '000'
  exit 7
fi
printf '%s' "${HEALTH_CODE}"
STUB

  cat > "${SANDBOX}/bin/docker" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  info)
    [[ "${DOCKER_DOWN}" == "1" ]] && exit 1
    exit 0 ;;
  ps)
    svc="$(printf '%s\n' "$@" | sed -n 's/^label=com.docker.compose.service=//p')"
    [[ " ${MISSING_SERVICES} " == *" ${svc} "* ]] && exit 0
    echo "id-${svc}" ;;
  inspect)
    id="${!#}"
    var="STATE_${id#id-}"
    echo "${!var}" ;;
esac
STUB
  chmod +x "${SANDBOX}/bin/curl" "${SANDBOX}/bin/docker"
}

# Runs the check once; sets RC.
run_check() {
  set +e
  PATH="${SANDBOX}/bin:${PATH}" \
    UPTIME_CONFIG="${SANDBOX}/config/test.env" \
    STATE_DIR="${SANDBOX}/state" \
    COMPOSE_PROJECT="test" \
    HEALTH_URL="http://localhost/lego/health" \
    bash "${SCRIPT}" >> "${SANDBOX}/out.log" 2>&1
  RC=$?
  set -e
}

tg_count() { grep -c '^CONFIG:' "${TG_LOG}" || true; }
state_value() { sed -n "s/^$1=//p" "${SANDBOX}/state/test.state"; }

expect_rc() { [[ "${RC}" == "$1" ]] || fail "expected exit $1, got ${RC} ($(tail -n1 "${SANDBOX}/out.log"))"; }
expect_tg() { [[ "$(tg_count)" == "$1" ]] || fail "expected $1 Telegram message(s), got $(tg_count)"; }

# ── 1. Healthy stack: exit 0, no message ──
make_sandbox healthy
run_check
expect_rc 0
expect_tg 0
[[ "$(state_value FAIL_COUNT)" == "0" ]] || fail "FAIL_COUNT should be 0"

# ── 2. Health 503: alarm only on the 2nd failure, once, then all-clear ──
make_sandbox threshold
export HEALTH_CODE=503 HEALTH_BODY='{"status": "unhealthy"}'
run_check
expect_rc 1
expect_tg 0
run_check
expect_rc 1
expect_tg 1
grep -q '🔴.*health HTTP 503' "${TG_LOG}" || fail "alarm text should name 'health HTTP 503'"
run_check
expect_tg 1  # unchanged failure inside the repeat window: no repeat
export HEALTH_CODE=200 HEALTH_BODY='{"status": "healthy"}'
run_check
expect_rc 0
expect_tg 2
grep -q '🟢.*health HTTP 503' "${TG_LOG}" || fail "all-clear should name the former cause"
[[ "$(state_value ALERTED)" == "0" ]] || fail "ALERTED should be reset after the all-clear"
run_check
expect_tg 2  # healthy again: no further message

# ── 3. Single failure below the threshold: no message at all ──
make_sandbox blip
export HEALTH_CODE=000
run_check
export HEALTH_CODE=200
run_check
expect_rc 0
expect_tg 0

# ── 4. Stopped worker (API still healthy) is detected ──
make_sandbox worker-stopped
export STATE_worker="exited none"
run_check
run_check
expect_tg 1
grep -q 'worker: exited' "${TG_LOG}" || fail "alarm should name 'worker: exited'"

# ── 5. Unhealthy beat and a missing container ──
make_sandbox unhealthy-and-missing
export STATE_beat="running unhealthy" MISSING_SERVICES="frontend"
run_check
run_check
expect_tg 1
grep -q 'beat: unhealthy' "${TG_LOG}" || fail "alarm should name 'beat: unhealthy'"
grep -q 'frontend: no container' "${TG_LOG}" || fail "alarm should name 'frontend: no container'"

# ── 6. Starting containers are not a failure ──
make_sandbox starting
export STATE_worker="running starting"
run_check
expect_rc 0

# ── 7. Changed cause while alarmed sends an update ──
make_sandbox changed
export STATE_worker="exited none"
run_check
run_check
export HEALTH_CODE=000
run_check
expect_tg 2
grep -q 'Störung verändert' "${TG_LOG}" || fail "second message should report the changed cause"

# ── 8. Unchanged failure is repeated after REALERT_SECONDS ──
make_sandbox realert
export HEALTH_CODE=502
run_check
run_check
expect_tg 1
sed -i 's/^LAST_ALERT_AT=.*/LAST_ALERT_AT=1/' "${SANDBOX}/state/test.state"
run_check
expect_tg 2
grep -q 'weiterhin gestört' "${TG_LOG}" || fail "repeat should say 'weiterhin gestört'"

# ── 9. Failed Telegram send is retried on the next run ──
make_sandbox send-retry
export HEALTH_CODE=000 TG_FAIL=1
run_check
run_check
[[ "$(state_value ALERTED)" == "0" ]] || fail "ALERTED must stay 0 when the send failed"
export TG_FAIL=0
run_check
expect_tg 2  # run 2: failed attempt (logged by the stub), run 3: the retry
[[ "$(state_value ALERTED)" == "1" ]] || fail "ALERTED should be 1 after a successful send"

# ── 10. Token travels via stdin, never in curl's argv ──
make_sandbox token-hidden
export HEALTH_CODE=000
run_check
run_check
grep -q "bot${FAKE_TOKEN}/sendMessage" "${TG_LOG}" || fail "token URL should be passed on stdin"
if grep -qF "${FAKE_TOKEN}" "${CURL_ARGS_LOG}"; then
  fail "token must not appear in curl's argument list"
fi
if grep -qF "${FAKE_TOKEN}" "${SANDBOX}/out.log"; then
  fail "token must not appear in the script output"
fi

# ── 11. Docker daemon unreachable ──
make_sandbox docker-down
export DOCKER_DOWN=1
run_check
run_check
grep -q 'docker not reachable' "${TG_LOG}" || fail "alarm should name 'docker not reachable'"

# ── 12. Missing Telegram config: no crash, failure is still counted ──
make_sandbox no-config
: > "${SANDBOX}/config/test.env"
export HEALTH_CODE=000
run_check
run_check
expect_rc 1
expect_tg 0
[[ "$(state_value FAIL_COUNT)" == "2" ]] || fail "FAIL_COUNT should be 2"
grep -q 'alarm NOT sent' "${SANDBOX}/out.log" || fail "missing config should be logged"

# ── 13. Invalid numeric setting aborts ──
make_sandbox bad-threshold
set +e
PATH="${SANDBOX}/bin:${PATH}" UPTIME_CONFIG="${SANDBOX}/config/test.env" STATE_DIR="${SANDBOX}/state" \
  COMPOSE_PROJECT=test FAIL_THRESHOLD=zero bash "${SCRIPT}" >/dev/null 2>&1
RC=$?
set -e
expect_rc 2

if (( FAILURES > 0 )); then
  echo "${FAILURES} uptime-check test assertion(s) failed" >&2
  exit 1
fi
echo "uptime-check tests passed"
