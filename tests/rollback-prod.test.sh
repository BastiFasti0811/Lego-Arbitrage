#!/usr/bin/env bash
#
# Tests for scripts/rollback-prod.sh (and its use of health-check-prod.sh).
#
# Each test builds a small real git repository with three commits:
#   GOOD   Alembic revision aaa (initial)
#   NEWER  Alembic revision bbb (down_revision aaa)
#   BROKEN no new revision; adds the rollback/health-check scripts, so checking
#          out GOOD or NEWER removes the very scripts that are running
# `docker` and `curl` are stubs: `compose build` records GIT_SHA, `compose up`
# makes the stubbed /health report that commit (unless HEALTH_BROKEN=1), and
# the postgres container answers the Alembic revision from DB_REV.
#
# Usage: bash tests/rollback-prod.test.sh

set -Eeuo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${TESTS_DIR}/.." && pwd)"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

FAILURES=0
CURRENT_TEST=""

fail() {
  echo "FAIL [${CURRENT_TEST}]: $1" >&2
  FAILURES=$((FAILURES + 1))
}

gitc() { git -C "${SANDBOX}/repo" -c user.email=t@t -c user.name=t "$@"; }

write_rev() { # write_rev <rev> <down|None>
  local down="None"
  [[ "$2" != "None" ]] && down="\"$2\""
  mkdir -p "${SANDBOX}/repo/backend/alembic/versions"
  cat > "${SANDBOX}/repo/backend/alembic/versions/$1_x.py" <<PY
"""rev $1"""
revision: str = "$1"
down_revision: str | None = ${down}
PY
}

make_sandbox() {
  SANDBOX="${TMP_ROOT}/$1"
  mkdir -p "${SANDBOX}/bin" "${SANDBOX}/state" "${SANDBOX}/tmp" "${SANDBOX}/repo"
  CALL_LOG="${SANDBOX}/calls.log"
  : > "${CALL_LOG}"
  git -C "${SANDBOX}/repo" init -q -b main
  printf '.env.prod\nbackend/.env\n' > "${SANDBOX}/repo/.gitignore"
  mkdir -p "${SANDBOX}/repo/backend"
  : > "${SANDBOX}/repo/.env.prod"
  : > "${SANDBOX}/repo/backend/.env"
  echo v1 > "${SANDBOX}/repo/app.txt"
  write_rev aaa None
  gitc add -A && gitc commit -q -m good
  GOOD="$(gitc rev-parse HEAD)"
  write_rev bbb aaa
  echo v2 > "${SANDBOX}/repo/app.txt"
  gitc add -A && gitc commit -q -m newer
  NEWER="$(gitc rev-parse HEAD)"
  mkdir -p "${SANDBOX}/repo/scripts"
  cp "${REPO_DIR}/scripts/rollback-prod.sh" "${REPO_DIR}/scripts/health-check-prod.sh" "${SANDBOX}/repo/scripts/"
  echo broken > "${SANDBOX}/repo/app.txt"
  gitc add -A && gitc commit -q -m broken
  BROKEN="$(gitc rev-parse HEAD)"
  echo "${BROKEN}" > "${SANDBOX}/state/running_sha"

  DB_REV="bbb"
  BUILD_FAIL=0
  HEALTH_BROKEN=0
  FLOCK_BUSY=0

  cat > "${SANDBOX}/bin/docker" <<'STUB'
#!/usr/bin/env bash
S="${STUB_STATE}"
case "$1" in
  compose)
    printf 'docker %s\n' "$*" >> "${CALL_LOG}"
    if [[ " $* " == *" build "* ]]; then
      printf 'build GIT_SHA=%s\n' "${GIT_SHA-<unset>}" >> "${CALL_LOG}"
      [[ "${BUILD_FAIL:-0}" == "1" ]] && exit 1
    fi
    if [[ " $* " == *" up "* ]]; then
      printf '%s\n' "${GIT_SHA}" > "${S}/running_sha"
    fi
    exit 0 ;;
  info) exit 0 ;;
  ps)
    for arg in "$@"; do
      [[ "${arg}" == label=com.docker.compose.service=* ]] && echo "id-${arg#label=com.docker.compose.service=}"
    done
    exit 0 ;;
  inspect) echo "running healthy 0 2020-01-01T00:00:00Z" ;;
  logs) exit 0 ;;
  exec)
    printf 'docker exec %s\n' "$2" >> "${CALL_LOG}"
    if [[ "$2" == "id-postgres" && -n "${DB_REV:-}" ]]; then
      printf '%s\n' ${DB_REV}
      exit 0
    fi
    exit 1 ;;
esac
exit 0
STUB

  cat > "${SANDBOX}/bin/curl" <<'STUB'
#!/usr/bin/env bash
S="${STUB_STATE}"
out=""
while (( $# > 0 )); do
  [[ "$1" == "-o" ]] && { out="$2"; shift; }
  shift
done
if [[ "${HEALTH_BROKEN:-0}" == "1" ]]; then
  printf '000'
  exit 7
fi
printf '{"status":"healthy","version":"%s"}\n' "$(cat "${S}/running_sha")" > "${out}"
printf '200'
STUB

  cat > "${SANDBOX}/bin/flock" <<'STUB'
#!/usr/bin/env bash
[[ "${FLOCK_BUSY:-0}" == "1" ]] && exit 1
exit 0
STUB
  chmod +x "${SANDBOX}/bin/"*
}

run_rollback() {
  RB_RC=0
  (
    cd "${SANDBOX}/repo"
    export PATH="${SANDBOX}/bin:${PATH}"
    export STUB_STATE="${SANDBOX}/state" CALL_LOG
    export DB_REV BUILD_FAIL HEALTH_BROKEN FLOCK_BUSY
    export TMPDIR="${SANDBOX}/tmp"
    export LOCK_FILE="${SANDBOX}/deploy.lock"
    export SKIP_FETCH=1 HEALTH_WAIT=2 WAIT_INTERVAL=1
    export HEALTH_URL="http://stub/health"
    unset REPO_DIR ROLLBACK_PRIVATE_COPY
    bash scripts/rollback-prod.sh "$@"
  ) > "${SANDBOX}/stdout.log" 2> "${SANDBOX}/stderr.log" || RB_RC=$?
}

expect_rc() {
  if [[ "${RB_RC}" -ne "$1" ]]; then
    fail "expected exit $1, got ${RB_RC} (stdout: $(tail -n 15 "${SANDBOX}/stdout.log" | tr '\n' '|') stderr: $(tail -n 15 "${SANDBOX}/stderr.log" | tr '\n' '|'))"
  fi
}
expect_out() {
  if ! grep -Eq -- "$1" "${SANDBOX}/stdout.log" "${SANDBOX}/stderr.log"; then
    fail "expected output matching '$1'"
  fi
}
expect_no_out() {
  if grep -Eq -- "$1" "${SANDBOX}/stdout.log" "${SANDBOX}/stderr.log"; then
    fail "unexpected output matching '$1'"
  fi
}
assert_called() {
  grep -Fq -- "$1" "${CALL_LOG}" || fail "expected call missing: $1"
}
assert_not_called() {
  if grep -Fq -- "$1" "${CALL_LOG}"; then fail "unexpected call: $1"; fi
}
head_is() {
  local head
  head="$(gitc rev-parse HEAD)"
  [[ "${head}" == "$1" ]] || fail "expected HEAD $1, got ${head}"
}
tmp_is_clean() {
  if [[ -n "$(ls -A "${SANDBOX}/tmp")" ]]; then
    fail "private script copy not cleaned up: $(ls "${SANDBOX}/tmp")"
  fi
}

# --- 1: the acceptance scenario ----------------------------------------------
# BROKEN is deployed but its API does not come up: the health check sees it,
# the rollback restores NEWER and the health check passes again.
CURRENT_TEST="broken release detected, rollback restores the previous commit"
make_sandbox t1
HEALTH_BROKEN=1
(
  export PATH="${SANDBOX}/bin:${PATH}" STUB_STATE="${SANDBOX}/state" HEALTH_BROKEN=1 HEALTH_URL="http://stub/health"
  bash "${REPO_DIR}/scripts/health-check-prod.sh" --expect-sha "${BROKEN}"
) > "${SANDBOX}/hc.log" 2>&1 && fail "health check must fail for the broken release"
grep -q "health: HTTP 000" "${SANDBOX}/hc.log" || fail "health check must name the unreachable API"
HEALTH_BROKEN=0
run_rollback "${NEWER}"
expect_rc 0
head_is "${NEWER}"
[[ -z "$(gitc branch --show-current)" ]] || fail "checkout must be a detached HEAD"
assert_called "build GIT_SHA=${NEWER}"
assert_called "up -d --remove-orphans postgres redis api worker beat frontend"
expect_out "Schema: database revision bbb matches"
expect_out "health-check: OK .*version ${NEWER}"
expect_out "Rollback to ${NEWER} finished after [0-9]+s"
tmp_is_clean

# --- 2: order: build before up, checkout before build --------------------------
CURRENT_TEST="build runs before up"
b="$(grep -n ' build' "${CALL_LOG}" | head -n1 | cut -d: -f1)"
u="$(grep -n ' up -d' "${CALL_LOG}" | head -n1 | cut -d: -f1)"
[[ -n "${b}" && -n "${u}" && "${b}" -lt "${u}" ]] || fail "expected build (line ${b}) before up (line ${u})"

# --- 3: dry run changes nothing ----------------------------------------------
CURRENT_TEST="--dry-run only prints the plan"
make_sandbox t3
run_rollback --dry-run "${GOOD}"
expect_rc 0
head_is "${BROKEN}"
assert_not_called "docker compose"
expect_out "\\[dry-run\\] would run: git checkout --quiet --detach ${GOOD}"
expect_out "\\[dry-run\\] would run: compose build"
expect_out "\\[dry-run\\] would run: compose up -d"
expect_out "\\[dry-run\\] would run: health-check-prod.sh --expect-sha ${GOOD}"
expect_out "SCHEMA AHEAD .*bbb"
expect_out "Nothing was changed"
tmp_is_clean

# --- 4: schema ahead warning on a real rollback ------------------------------
CURRENT_TEST="target older than the database: warn, do not downgrade"
make_sandbox t4
run_rollback "${GOOD}"
expect_rc 0
head_is "${GOOD}"
expect_out "WARN: SCHEMA AHEAD — the database is at Alembic revision bbb"
expect_out "Migrations are NOT rolled back"
assert_not_called "alembic"

# --- 5: schema behind --------------------------------------------------------
CURRENT_TEST="target newer than the database"
make_sandbox t5
DB_REV="aaa"
run_rollback "${NEWER}"
expect_rc 0
expect_out "SCHEMA BEHIND"

# --- 6: database not readable ------------------------------------------------
CURRENT_TEST="alembic_version unreadable is only a warning"
make_sandbox t6
DB_REV=""
run_rollback "${NEWER}"
expect_rc 0
expect_out "could not read alembic_version"

# --- 7: build failure restores the checkout, containers untouched ------------
CURRENT_TEST="failed build: previous checkout restored, no up"
make_sandbox t7
BUILD_FAIL=1
run_rollback "${NEWER}"
expect_rc 1
head_is "${BROKEN}"
[[ "$(gitc branch --show-current)" == "main" ]] || fail "branch main must be restored"
assert_not_called " up -d"
expect_out "running containers were NOT touched"
tmp_is_clean

# --- 8: health check still red after the rollback ----------------------------
CURRENT_TEST="rollback target is broken as well"
make_sandbox t8
HEALTH_BROKEN=1
run_rollback "${NEWER}"
expect_rc 1
expect_out "FAILED its health check"
assert_called " logs --tail=60 api worker"
tmp_is_clean

# --- 9: preconditions ----------------------------------------------------------
CURRENT_TEST="unknown commit"
make_sandbox t9
run_rollback "deadbeef"
expect_rc 2
expect_out "Unknown commit 'deadbeef'"
assert_not_called "docker compose"

CURRENT_TEST="dirty checkout"
make_sandbox t9b
echo dirty >> "${SANDBOX}/repo/app.txt"
run_rollback "${NEWER}"
expect_rc 2
expect_out "local changes"
head_is "${BROKEN}"
assert_not_called "docker compose"

CURRENT_TEST="deploy lock held"
make_sandbox t9c
FLOCK_BUSY=1
run_rollback "${NEWER}"
expect_rc 2
expect_out "Another deploy or rollback is running"
assert_not_called "docker"

CURRENT_TEST="lock held by deploy-prod.sh --auto-rollback"
make_sandbox t9d
FLOCK_BUSY=1
(
  export DEPLOY_LOCK_HELD=1
  run_rollback "${NEWER}"
  [[ "${RB_RC}" -eq 0 ]] || echo "rc=${RB_RC}" > "${SANDBOX}/rc"
)
[[ ! -f "${SANDBOX}/rc" ]] || fail "expected success with DEPLOY_LOCK_HELD=1, got $(cat "${SANDBOX}/rc")"

CURRENT_TEST="missing env file"
make_sandbox t9e
rm "${SANDBOX}/repo/.env.prod"
run_rollback "${NEWER}"
expect_rc 2
expect_out "Missing .env.prod"

CURRENT_TEST="usage"
make_sandbox t9f
run_rollback
expect_rc 2
run_rollback --bogus "${NEWER}"
expect_rc 2

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "${FAILURES} assertion(s) failed." >&2
  exit 1
fi
echo "All rollback-prod.sh tests passed."
