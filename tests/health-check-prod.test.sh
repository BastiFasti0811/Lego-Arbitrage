#!/usr/bin/env bash
#
# Tests for scripts/health-check-prod.sh.
#
# `docker` and `curl` are stubs driven by files in a per-test sandbox:
#   state/<service>   "<status> <health> <restart count> <started at>" of the
#                     service's container; missing file = no container
#   state/worker.log  output of `docker logs` for the worker
#   state/health      "<http code> <body>" answered by curl; with
#                     state/health.N present, attempt N answers that instead
# No real containers or HTTP requests are involved.
#
# Usage: bash tests/health-check-prod.test.sh

set -Eeuo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${TESTS_DIR}/.." && pwd)"
SCRIPT="${REPO_DIR}/scripts/health-check-prod.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

FAILURES=0
CURRENT_TEST=""
SHA="0123456789abcdef0123456789abcdef01234567"
SERVICES_ALL="api worker beat frontend postgres redis"

fail() {
  echo "FAIL [${CURRENT_TEST}]: $1" >&2
  FAILURES=$((FAILURES + 1))
}

iso_ago() { date -u -d "@$(( $(date +%s) - $1 ))" +%Y-%m-%dT%H:%M:%S.000000000Z; }

make_sandbox() {
  SANDBOX="${TMP_ROOT}/$1"
  mkdir -p "${SANDBOX}/bin" "${SANDBOX}/state"
  local svc health
  for svc in ${SERVICES_ALL}; do
    health="healthy"
    [[ "${svc}" == "frontend" ]] && health="none"
    echo "running ${health} 0 $(iso_ago 3600)" > "${SANDBOX}/state/${svc}"
  done
  printf '[INFO] celery@worker ready.\n' > "${SANDBOX}/state/worker.log"
  printf '200 {"status":"healthy","version":"%s","service":"lego-arbitrage-api","checks":{"database":"ok","redis":"ok"}}\n' "${SHA}" \
    > "${SANDBOX}/state/health"

  # docker: ps -aq --filter ...service=<svc> prints the id "id-<svc>" when the
  # state file exists; inspect id-<svc> prints the state line; logs prints the
  # worker log; info succeeds unless state/docker-down exists.
  cat > "${SANDBOX}/bin/docker" <<'STUB'
#!/usr/bin/env bash
S="${STUB_STATE}"
case "$1" in
  info) [[ -e "${S}/docker-down" ]] && exit 1; exit 0 ;;
  ps)
    for arg in "$@"; do
      if [[ "${arg}" == label=com.docker.compose.service=* ]]; then
        svc="${arg#label=com.docker.compose.service=}"
        [[ -f "${S}/${svc}" ]] && echo "id-${svc}"
        [[ -f "${S}/${svc}.extra" ]] && echo "id2-${svc}"
      fi
    done
    exit 0 ;;
  inspect)
    id="${@: -1}"
    cat "${S}/${id#id-}" ;;
  logs)
    cat "${S}/worker.log" ;;
  *) exit 0 ;;
esac
STUB

  # curl: answers state/health (or state/health.<attempt>), writes the body to
  # the -o file and prints the code (the script uses -w '%{http_code}').
  cat > "${SANDBOX}/bin/curl" <<'STUB'
#!/usr/bin/env bash
S="${STUB_STATE}"
n=$(( $(cat "${S}/curl.count" 2>/dev/null || echo 0) + 1 ))
echo "${n}" > "${S}/curl.count"
src="${S}/health"
[[ -f "${S}/health.${n}" ]] && src="${S}/health.${n}"
out=""
while (( $# > 0 )); do
  [[ "$1" == "-o" ]] && { out="$2"; shift; }
  shift
done
read -r code body < "${src}"
[[ -n "${out}" ]] && printf '%s\n' "${body}" > "${out}"
printf '%s' "${code}"
STUB
  chmod +x "${SANDBOX}/bin/docker" "${SANDBOX}/bin/curl"
}

run_check() {
  CHECK_RC=0
  (
    export PATH="${SANDBOX}/bin:${PATH}"
    export STUB_STATE="${SANDBOX}/state"
    export WAIT_INTERVAL=1
    bash "${SCRIPT}" "$@"
  ) > "${SANDBOX}/stdout.log" 2> "${SANDBOX}/stderr.log" || CHECK_RC=$?
}

expect_rc() {
  if [[ "${CHECK_RC}" -ne "$1" ]]; then
    fail "expected exit $1, got ${CHECK_RC} (stdout: $(tr '\n' '|' < "${SANDBOX}/stdout.log") stderr: $(tr '\n' '|' < "${SANDBOX}/stderr.log"))"
  fi
}

expect_out() { # pattern in stdout+stderr
  if ! grep -Eq -- "$1" "${SANDBOX}/stdout.log" "${SANDBOX}/stderr.log"; then
    fail "expected output matching '$1'"
  fi
}

# --- 1: everything healthy ---------------------------------------------------
CURRENT_TEST="all six containers running, /health with the expected version"
make_sandbox t1
run_check --expect-sha "${SHA}"
expect_rc 0
expect_out "health-check: OK"
expect_out "OK    frontend: running$"
expect_out "version ${SHA}"

# --- 2: short SHA prefix is accepted ------------------------------------------
CURRENT_TEST="expected SHA given as 7-char prefix"
make_sandbox t2
run_check --expect-sha "${SHA:0:7}"
expect_rc 0

# --- 3: wrong version (old release still running) ----------------------------
CURRENT_TEST="/health reports another commit"
make_sandbox t3
run_check --expect-sha "fedcba9"
expect_rc 1
expect_out "FAIL  health: version ${SHA}, expected fedcba9"

# --- 4: API answers 503 ------------------------------------------------------
CURRENT_TEST="/health 503 unhealthy"
make_sandbox t4
echo '503 {"status":"unhealthy","version":"x","checks":{"database":"error","redis":"ok"}}' > "${SANDBOX}/state/health"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "health: HTTP 503 .*status unhealthy"

# --- 5: API not reachable at all (curl prints 000) ---------------------------
CURRENT_TEST="/health unreachable"
make_sandbox t5
echo '000 ' > "${SANDBOX}/state/health"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "health: HTTP 000"

# --- 6: API container in a restart loop (import error at startup) ------------
CURRENT_TEST="api restarting"
make_sandbox t6
echo "restarting none 7 $(iso_ago 5)" > "${SANDBOX}/state/api"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "FAIL  api: restarting \\(7 restarts\\)"

CURRENT_TEST="api momentarily running but restarted 5 times within the window"
make_sandbox t6b
echo "running starting 5 $(iso_ago 20)" > "${SANDBOX}/state/api"
sed -i 's/ starting / none /' "${SANDBOX}/state/api"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "FAIL  api: restart loop \\(5 restarts, last start [0-9]+s ago\\)"

# --- 7: a single old restart is only a warning -------------------------------
CURRENT_TEST="one restart long ago is a warning"
make_sandbox t7
echo "running healthy 1 $(iso_ago 7200)" > "${SANDBOX}/state/worker"
run_check --expect-sha "${SHA}"
expect_rc 0
expect_out "WARN  worker: 1 restart"

# --- 8: unhealthy and still-starting containers ------------------------------
CURRENT_TEST="worker unhealthy"
make_sandbox t8
echo "running unhealthy 0 $(iso_ago 600)" > "${SANDBOX}/state/worker"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "FAIL  worker: unhealthy"

CURRENT_TEST="beat healthcheck still starting"
make_sandbox t8b
echo "running starting 0 $(iso_ago 10)" > "${SANDBOX}/state/beat"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "FAIL  beat: healthcheck still starting"

# --- 9: missing / exited / duplicate containers ------------------------------
CURRENT_TEST="redis container missing"
make_sandbox t9
rm "${SANDBOX}/state/redis"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "FAIL  redis: no container"

CURRENT_TEST="frontend exited"
make_sandbox t9b
echo "exited none 0 $(iso_ago 600)" > "${SANDBOX}/state/frontend"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "FAIL  frontend: exited"

CURRENT_TEST="two api containers"
make_sandbox t9c
touch "${SANDBOX}/state/api.extra"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "FAIL  api: 2 containers"

# --- 10: worker traceback ----------------------------------------------------
CURRENT_TEST="worker log with a traceback"
make_sandbox t10
cat > "${SANDBOX}/state/worker.log" <<'LOG'
[INFO] Task app.tasks.scan started
Traceback (most recent call last):
  File "/app/app/tasks/scan.py", line 12, in run
ImportError: cannot import name 'foo'
LOG
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "FAIL  worker log: 1 traceback"
expect_out "ImportError: cannot import name 'foo'"

# --- 11: docker not reachable ------------------------------------------------
CURRENT_TEST="docker daemon down"
make_sandbox t11
touch "${SANDBOX}/state/docker-down"
run_check --expect-sha "${SHA}"
expect_rc 1
expect_out "FAIL  docker not reachable"

# --- 12: --wait passes once the API is up ------------------------------------
CURRENT_TEST="--wait retries until the new version answers"
make_sandbox t12
echo '000 ' > "${SANDBOX}/state/health.1"
printf '200 {"status":"healthy","version":"0.2.0"}\n' > "${SANDBOX}/state/health.2"
run_check --expect-sha "${SHA}" --wait 10
expect_rc 0
expect_out "attempt 1: health: HTTP 000"
expect_out "attempt 2: health: version 0.2.0"
expect_out "health-check: OK .*attempt 3"

CURRENT_TEST="--wait gives up after the deadline"
make_sandbox t12b
echo '503 {"status":"unhealthy"}' > "${SANDBOX}/state/health"
run_check --expect-sha "${SHA}" --wait 2
expect_rc 1
expect_out "health-check: FAILED"

CURRENT_TEST="--wait stops early on a restart loop"
make_sandbox t12c
echo "restarting none 9 $(iso_ago 3)" > "${SANDBOX}/state/api"
start=$(date +%s)
run_check --expect-sha "${SHA}" --wait 60
expect_rc 1
expect_out "will not heal by waiting"
(( $(date +%s) - start < 10 )) || fail "--wait must not use its full budget on a restart loop"

CURRENT_TEST="--wait stops early on a worker traceback"
make_sandbox t12d
printf 'Traceback (most recent call last):\nValueError: x\n' > "${SANDBOX}/state/worker.log"
start=$(date +%s)
run_check --expect-sha "${SHA}" --wait 60
expect_rc 1
expect_out "will not heal by waiting"
(( $(date +%s) - start < 10 )) || fail "--wait must not use its full budget on a traceback"

CURRENT_TEST="--wait keeps waiting while a container restarts below the limit"
make_sandbox t12e
echo "restarting none 1 $(iso_ago 3)" > "${SANDBOX}/state/api"
run_check --expect-sha "${SHA}" --wait 2
expect_rc 1
if grep -q "will not heal" "${SANDBOX}/stderr.log"; then
  fail "a single restart must not end the wait early"
fi

# --- 13: --no-version-check and usage errors ---------------------------------
CURRENT_TEST="--no-version-check accepts any reported version"
make_sandbox t13
run_check --no-version-check
expect_rc 0
expect_out "no version check"

CURRENT_TEST="--no-version-check still needs a version"
make_sandbox t13b
echo '200 {"status":"healthy"}' > "${SANDBOX}/state/health"
run_check --no-version-check
expect_rc 1
expect_out "health: no version reported"

CURRENT_TEST="invalid --expect-sha"
make_sandbox t13c
run_check --expect-sha "main"
expect_rc 2

CURRENT_TEST="unknown argument"
make_sandbox t13d
run_check --bogus
expect_rc 2

# --- 14: default expected SHA comes from git HEAD of REPO_DIR ----------------
CURRENT_TEST="default --expect-sha is git HEAD"
make_sandbox t14
git -C "${SANDBOX}" init -q repo
git -C "${SANDBOX}/repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
head_sha="$(git -C "${SANDBOX}/repo" rev-parse HEAD)"
printf '200 {"status":"healthy","version":"%s"}\n' "${head_sha}" > "${SANDBOX}/state/health"
REPO_DIR="${SANDBOX}/repo" run_check
expect_rc 0
expect_out "version ${head_sha}"

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "${FAILURES} assertion(s) failed." >&2
  exit 1
fi
echo "All health-check-prod.sh tests passed."
