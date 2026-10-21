#!/usr/bin/env bash
#
# Tests for scripts/deploy-prod.sh.
#
# Runs the real deploy script inside a throwaway sandbox where `docker` and
# `git` are stubs that record every invocation to a call log. The assertions
# check the call sequence and the abort behavior — no real containers or
# repositories are touched.
#
# Usage: bash tests/deploy-prod.test.sh

set -Eeuo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${TESTS_DIR}/.." && pwd)"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

FAILURES=0
CURRENT_TEST=""

# Expected calls, exactly as the deploy script must issue them.
PULL_CALL="git pull --ff-only"
BUILD_CALL="docker compose --env-file .env.prod -f docker-compose.prod.yml build"
MIGRATE_CALL="docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm -T api alembic upgrade head"
UP_CALL="docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --remove-orphans postgres redis api worker beat frontend"
HEALTH_CALL="docker exec lego-api-prod curl -fsS http://127.0.0.1:8000/health"
# Commit the git stub reports for `git rev-parse HEAD` after `git pull`;
# before the pull it reports PREV_STUB_SHA (the rollback target).
STUB_SHA="0123456789abcdef0123456789abcdef01234567"
PREV_STUB_SHA="fedcba9876543210fedcba9876543210fedcba98"

fail() {
  echo "FAIL [${CURRENT_TEST}]: $1" >&2
  FAILURES=$((FAILURES + 1))
}

make_sandbox() {
  SANDBOX="${TMP_ROOT}/$1"
  mkdir -p "${SANDBOX}/repo/scripts" "${SANDBOX}/repo/backend" "${SANDBOX}/bin"
  cp "${REPO_DIR}/scripts/deploy-prod.sh" "${SANDBOX}/repo/scripts/deploy-prod.sh"
  : > "${SANDBOX}/repo/.env.prod"
  : > "${SANDBOX}/repo/backend/.env"
  CALL_LOG="${SANDBOX}/calls.log"
  : > "${CALL_LOG}"
  # Per-test knobs for the health stub, reset for every sandbox:
  # HEALTH_VERSION is the version /health reports (default: the deployed
  # commit), HEALTH_FAIL=1 makes curl fail like on a 503.
  HEALTH_VERSION="${STUB_SHA}"
  HEALTH_FAIL=0
  # FULL_CHECK_FAIL=1 makes the stubbed scripts/health-check-prod.sh fail,
  # ROLLBACK_FAIL=1 the stubbed scripts/rollback-prod.sh.
  FULL_CHECK_FAIL=0
  ROLLBACK_FAIL=0

  cat > "${SANDBOX}/repo/scripts/health-check-prod.sh" <<'STUB'
#!/usr/bin/env bash
printf 'health-check-prod %s\n' "$*" >> "${CALL_LOG}"
[[ "${FULL_CHECK_FAIL:-0}" == "1" ]] && exit 1
exit 0
STUB
  cat > "${SANDBOX}/repo/scripts/rollback-prod.sh" <<'STUB'
#!/usr/bin/env bash
printf 'rollback-prod %s lock_held=%s\n' "$*" "${DEPLOY_LOCK_HELD:-0}" >> "${CALL_LOG}"
[[ "${ROLLBACK_FAIL:-0}" == "1" ]] && exit 1
exit 0
STUB

  # The git stub reports the branch from GIT_BRANCH (default main; set it to
  # an empty string to simulate a detached HEAD).
  cat > "${SANDBOX}/bin/git" <<'STUB'
#!/usr/bin/env bash
printf 'git %s\n' "$*" >> "${CALL_LOG}"
if [[ "$*" == "branch --show-current" ]]; then
  printf '%s\n' "${GIT_BRANCH-main}"
fi
if [[ "$*" == "rev-parse HEAD" ]]; then
  if grep -q '^git pull' "${CALL_LOG}"; then
    printf '%s\n' "${STUB_SHA}"
  else
    printf '%s\n' "${PREV_STUB_SHA}"
  fi
fi
exit 0
STUB

  # The docker stub succeeds by default; MIGRATION_FAIL=1 fails the alembic
  # migration call, BUILD_FAIL=1 fails the compose build call, so the abort
  # paths can be exercised. The build call also logs the GIT_SHA it sees, and
  # the health call prints a /health body (see HEALTH_VERSION/HEALTH_FAIL).
  cat > "${SANDBOX}/bin/docker" <<'STUB'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "${CALL_LOG}"
if [[ "$*" == *"alembic upgrade head"* && "${MIGRATION_FAIL:-0}" == "1" ]]; then
  exit 7
fi
if [[ "$*" == *" build"* ]]; then
  printf 'build-env GIT_SHA=%s\n' "${GIT_SHA-<unset>}" >> "${CALL_LOG}"
  if [[ "${BUILD_FAIL:-0}" == "1" ]]; then
    exit 3
  fi
fi
if [[ "$*" == *"curl -fsS http://127.0.0.1:8000/health"* ]]; then
  if [[ "${HEALTH_FAIL:-0}" == "1" ]]; then
    exit 22
  fi
  printf '{"status":"healthy","version":"%s","service":"lego-arbitrage-api"}\n' "${HEALTH_VERSION}"
fi
exit 0
STUB

  # The flock stub makes the lock path testable on systems without a real
  # flock (e.g. Git Bash); FLOCK_BUSY=1 simulates a lock held by another run.
  cat > "${SANDBOX}/bin/flock" <<'STUB'
#!/usr/bin/env bash
if [[ "${FLOCK_BUSY:-0}" == "1" ]]; then
  exit 1
fi
exit 0
STUB

  chmod +x "${SANDBOX}/bin/git" "${SANDBOX}/bin/docker" "${SANDBOX}/bin/flock"
}

run_deploy() { # usage: run_deploy [migration_fail] [build_fail] [flock_busy] [git_branch] [deploy args...]
  local migration_fail="${1:-0}" build_fail="${2:-0}" flock_busy="${3:-0}"
  local git_branch="${4-main}"
  shift $(( $# < 4 ? $# : 4 ))
  DEPLOY_RC=0
  (
    cd "${SANDBOX}/repo"
    export PATH="${SANDBOX}/bin:${PATH}"
    export CALL_LOG
    export MIGRATION_FAIL="${migration_fail}" BUILD_FAIL="${build_fail}"
    export FLOCK_BUSY="${flock_busy}"
    export GIT_BRANCH="${git_branch}"
    export LOCK_FILE="${SANDBOX}/deploy.lock"
    export STUB_SHA PREV_STUB_SHA HEALTH_VERSION HEALTH_FAIL
    export FULL_CHECK_FAIL ROLLBACK_FAIL
    export HEALTH_ATTEMPTS=3 HEALTH_INTERVAL=0
    bash scripts/deploy-prod.sh "$@"
  ) > "${SANDBOX}/stdout.log" 2> "${SANDBOX}/stderr.log" || DEPLOY_RC=$?
}

# Line number of the first call matching the given fixed string, empty if absent.
line_of() {
  local line
  line="$(grep -Fn -- "$1" "${CALL_LOG}" | head -n 1 | cut -d: -f1)" || true
  printf '%s' "${line}"
}

assert_called() {
  if [[ -z "$(line_of "$1")" ]]; then
    fail "expected call missing: $1"
  fi
}

assert_not_called() {
  if grep -Fq -- "$1" "${CALL_LOG}"; then
    fail "unexpected call present: $1"
  fi
}

assert_order() {
  local a b
  a="$(line_of "$1")"
  b="$(line_of "$2")"
  if [[ -z "${a}" || -z "${b}" ]]; then
    fail "cannot check order, call missing: '$1' before '$2'"
    return
  fi
  if (( a >= b )); then
    fail "wrong order: '$1' (line ${a}) must run before '$2' (line ${b})"
  fi
}

# --- Test 1: successful deploy builds, migrates, then restarts -------------

CURRENT_TEST="success: pull -> build -> migrate -> up -> healthcheck"
make_sandbox t1
run_deploy

if [[ "${DEPLOY_RC}" -ne 0 ]]; then
  fail "expected exit 0, got ${DEPLOY_RC} (stderr: $(cat "${SANDBOX}/stderr.log"))"
fi
assert_called "${PULL_CALL}"
assert_called "${BUILD_CALL}"
assert_called "${MIGRATE_CALL}"
assert_called "${UP_CALL}"
assert_called "${HEALTH_CALL}"
assert_not_called "--build"
assert_order "${PULL_CALL}" "${BUILD_CALL}"
assert_order "${BUILD_CALL}" "${MIGRATE_CALL}"
assert_order "${MIGRATE_CALL}" "${UP_CALL}"
assert_order "${UP_CALL}" "${HEALTH_CALL}"
assert_called "build-env GIT_SHA=${STUB_SHA}"
if ! grep -Fq "version ${STUB_SHA}" "${SANDBOX}/stdout.log"; then
  fail "expected the verified version on stdout"
fi
assert_called "health-check-prod --expect-sha ${STUB_SHA} --wait"
assert_order "${HEALTH_CALL}" "health-check-prod --expect-sha"
assert_not_called "rollback-prod"
if ! grep -q "finished successfully" "${SANDBOX}/stdout.log"; then
  fail "expected the success line on stdout"
fi

# --- Test 2: failed migration aborts before any container is restarted -----

CURRENT_TEST="failure: broken migration aborts deploy, no restart"
make_sandbox t2
run_deploy 1

if [[ "${DEPLOY_RC}" -eq 0 ]]; then
  fail "expected non-zero exit when the migration fails"
fi
assert_called "${BUILD_CALL}"
assert_called "${MIGRATE_CALL}"
assert_not_called " up -d"
assert_not_called "docker exec lego-api-prod"
if ! grep -qi "migration step failed" "${SANDBOX}/stderr.log"; then
  fail "expected a 'migration step failed' message on stderr"
fi

# --- Test 3: failed build aborts before migrations and restart --------------

CURRENT_TEST="failure: broken build aborts deploy before migrate/up"
make_sandbox t3
run_deploy 0 1

if [[ "${DEPLOY_RC}" -eq 0 ]]; then
  fail "expected non-zero exit when the build fails"
fi
assert_called "${BUILD_CALL}"
assert_not_called "alembic upgrade head"
assert_not_called " up -d"
assert_not_called "docker exec lego-api-prod"

# --- Test 4: held deploy lock aborts before any action ----------------------

CURRENT_TEST="failure: held deploy lock aborts before any git/docker call"
make_sandbox t4
run_deploy 0 0 1

if [[ "${DEPLOY_RC}" -eq 0 ]]; then
  fail "expected non-zero exit when the deploy lock is held"
fi
if [[ -s "${CALL_LOG}" ]]; then
  fail "expected no git/docker calls, got: $(tr '\n' '; ' < "${CALL_LOG}")"
fi
if ! grep -qi "another deploy" "${SANDBOX}/stderr.log"; then
  fail "expected an 'another deploy' message on stderr"
fi

# --- Test 5: checkout not on main aborts before pull/build ------------------

for branch in "feature/x" ""; do
  CURRENT_TEST="failure: checkout on '${branch:-detached HEAD}' aborts before pull"
  make_sandbox "t5-${branch//\//-}"
  run_deploy 0 0 0 "${branch}"

  if [[ "${DEPLOY_RC}" -eq 0 ]]; then
    fail "expected non-zero exit when the checkout is not on main"
  fi
  assert_not_called "${PULL_CALL}"
  assert_not_called "docker "
  if ! grep -qi "expected 'main'" "${SANDBOX}/stderr.log"; then
    fail "expected a branch mismatch message on stderr"
  fi
done

# --- Test 6: API healthy but still on another version fails the deploy ------

CURRENT_TEST="failure: /health reports a different version than the deployed commit"
make_sandbox t6
HEALTH_VERSION="0.2.0"
run_deploy

if [[ "${DEPLOY_RC}" -eq 0 ]]; then
  fail "expected non-zero exit when /health reports another version"
fi
if ! grep -q "does not report the deployed commit ${STUB_SHA}" "${SANDBOX}/stderr.log"; then
  fail "expected a version mismatch message on stderr"
fi
if grep -q "finished successfully" "${SANDBOX}/stdout.log"; then
  fail "a version mismatch must not report success"
fi
assert_not_called "rollback-prod"
if ! grep -Fq "rollback-prod.sh ${PREV_STUB_SHA}" "${SANDBOX}/stderr.log"; then
  fail "expected the rollback command for the previous commit on stderr"
fi

# --- Test 7: /health stays unhealthy (503) fails the deploy -----------------

CURRENT_TEST="failure: /health never answers 200 (database or redis down)"
make_sandbox t7
HEALTH_FAIL=1
run_deploy

if [[ "${DEPLOY_RC}" -eq 0 ]]; then
  fail "expected non-zero exit when /health keeps failing"
fi
if ! grep -q "did not pass within" "${SANDBOX}/stderr.log"; then
  fail "expected a healthcheck timeout message on stderr"
fi

# --- Test 8: full health check fails -> deploy fails, rollback only hinted -

CURRENT_TEST="failure: full health check red, no --auto-rollback"
make_sandbox t8
FULL_CHECK_FAIL=1
run_deploy

if [[ "${DEPLOY_RC}" -eq 0 ]]; then
  fail "expected non-zero exit when the full health check fails"
fi
assert_called "health-check-prod --expect-sha ${STUB_SHA}"
assert_not_called "rollback-prod"
if grep -q "finished successfully" "${SANDBOX}/stdout.log"; then
  fail "a failed full check must not report success"
fi

# --- Test 9: --auto-rollback rolls back to the commit before the pull ------

CURRENT_TEST="auto-rollback: red full check rolls back to the previous commit"
make_sandbox t9
FULL_CHECK_FAIL=1
run_deploy 0 0 0 main --auto-rollback

if [[ "${DEPLOY_RC}" -eq 0 ]]; then
  fail "a rolled back deploy must still exit non-zero"
fi
assert_called "rollback-prod ${PREV_STUB_SHA} lock_held=1"
assert_order "health-check-prod --expect-sha" "rollback-prod"
if ! grep -q "Rolled back to ${PREV_STUB_SHA}" "${SANDBOX}/stderr.log"; then
  fail "expected the rollback confirmation on stderr"
fi

# --- Test 10: --auto-rollback after a red API poll, rollback itself fails --

CURRENT_TEST="auto-rollback: API poll red, failing rollback is reported"
make_sandbox t10
HEALTH_FAIL=1
ROLLBACK_FAIL=1
run_deploy 0 0 0 main --auto-rollback

if [[ "${DEPLOY_RC}" -eq 0 ]]; then
  fail "expected non-zero exit"
fi
assert_called "rollback-prod ${PREV_STUB_SHA}"
assert_not_called "health-check-prod"
if ! grep -q "failed as well" "${SANDBOX}/stderr.log"; then
  fail "expected the 'rollback failed as well' message"
fi

# --- Test 11: --auto-rollback is not triggered by a failed build ------------

CURRENT_TEST="auto-rollback: failed build keeps old containers, no rollback"
make_sandbox t11
run_deploy 0 1 0 main --auto-rollback

if [[ "${DEPLOY_RC}" -eq 0 ]]; then
  fail "expected non-zero exit when the build fails"
fi
assert_not_called "rollback-prod"

# --- Test 12: unknown argument is rejected before anything runs -------------

CURRENT_TEST="failure: unknown argument"
make_sandbox t12
run_deploy 0 0 0 main --bogus

if [[ "${DEPLOY_RC}" -ne 2 ]]; then
  fail "expected exit 2 for an unknown argument, got ${DEPLOY_RC}"
fi
if [[ -s "${CALL_LOG}" ]]; then
  fail "expected no calls for an unknown argument"
fi

# --- Test 13: env files and backups lose group/other access before the build

mode_of() { stat -c '%a' "$1"; }

CURRENT_TEST="secrets: world-readable env files are tightened to owner-only"
make_sandbox t13
: > "${SANDBOX}/repo/backend/.env.bak-20260930"
: > "${SANDBOX}/repo/backend/.env.example"
chmod 644 "${SANDBOX}/repo/.env.prod" "${SANDBOX}/repo/backend/.env" \
  "${SANDBOX}/repo/backend/.env.bak-20260930" "${SANDBOX}/repo/backend/.env.example"
run_deploy

if [[ "${DEPLOY_RC}" -ne 0 ]]; then
  fail "expected exit 0, got ${DEPLOY_RC} (stderr: $(cat "${SANDBOX}/stderr.log"))"
fi
for f in .env.prod backend/.env backend/.env.bak-20260930; do
  mode="$(mode_of "${SANDBOX}/repo/${f}")"
  if [[ "${mode}" != "600" ]]; then
    fail "expected ${f} to be 600 after the deploy, got ${mode}"
  fi
done
if [[ "$(mode_of "${SANDBOX}/repo/backend/.env.example")" != "644" ]]; then
  fail "the tracked template backend/.env.example must keep its mode"
fi
if ! grep -Fq "backend/.env.bak-20260930" "${SANDBOX}/stderr.log"; then
  fail "expected a warning naming the env backup inside the repo directory"
fi

CURRENT_TEST="secrets: no backup, no warning"
make_sandbox t13b
run_deploy
if grep -qi "env backups" "${SANDBOX}/stderr.log"; then
  fail "no backup present, but a backup warning was printed"
fi

# --- Result -----------------------------------------------------------------

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "${FAILURES} assertion(s) failed." >&2
  exit 1
fi
echo "All deploy-prod.sh tests passed."
