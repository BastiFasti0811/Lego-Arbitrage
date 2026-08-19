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

  cat > "${SANDBOX}/bin/git" <<'STUB'
#!/usr/bin/env bash
printf 'git %s\n' "$*" >> "${CALL_LOG}"
exit 0
STUB

  # The docker stub succeeds by default; with MIGRATION_FAIL=1 the alembic
  # migration call fails so the abort path can be exercised.
  cat > "${SANDBOX}/bin/docker" <<'STUB'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "${CALL_LOG}"
if [[ "$*" == *"alembic upgrade head"* && "${MIGRATION_FAIL:-0}" == "1" ]]; then
  exit 7
fi
exit 0
STUB

  chmod +x "${SANDBOX}/bin/git" "${SANDBOX}/bin/docker"
}

run_deploy() {
  DEPLOY_RC=0
  (
    cd "${SANDBOX}/repo"
    export PATH="${SANDBOX}/bin:${PATH}"
    export CALL_LOG
    export MIGRATION_FAIL="${1:-0}"
    bash scripts/deploy-prod.sh
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
run_deploy 0

if [[ "${DEPLOY_RC}" -ne 0 ]]; then
  fail "expected exit 0, got ${DEPLOY_RC} (stderr: $(cat "${SANDBOX}/stderr.log"))"
fi
assert_called "${PULL_CALL}"
assert_called "${BUILD_CALL}"
assert_called "${MIGRATE_CALL}"
assert_called "${UP_CALL}"
assert_called "${HEALTH_CALL}"
assert_order "${PULL_CALL}" "${BUILD_CALL}"
assert_order "${BUILD_CALL}" "${MIGRATE_CALL}"
assert_order "${MIGRATE_CALL}" "${UP_CALL}"
assert_order "${UP_CALL}" "${HEALTH_CALL}"

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
if ! grep -qi "migration failed" "${SANDBOX}/stderr.log"; then
  fail "expected a 'migration failed' message on stderr"
fi

# --- Result -----------------------------------------------------------------

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "${FAILURES} assertion(s) failed." >&2
  exit 1
fi
echo "All deploy-prod.sh tests passed."
