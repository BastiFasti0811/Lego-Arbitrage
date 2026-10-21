#!/usr/bin/env bash
#
# Secret hygiene of the repository (docs/deploy.md#secrets):
#   1. .gitignore ignores env files and their backups, but not the templates.
#   2. backend/.dockerignore keeps env files, tests/ and data/ out of the
#      backend image; the root .dockerignore keeps env files out of the
#      frontend build context ".".
#
# Part 2 runs a real BuildKit build of a FROM-scratch image against a
# throwaway context that carries the repository's .dockerignore files, then
# exports the result to a directory: nothing is pulled, no image is stored.
# Without docker it is skipped, unless SECRET_HYGIENE_DOCKER=1 demands it (CI).
#
# Usage: bash tests/secret-hygiene.test.sh

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

# --- 1. .gitignore ----------------------------------------------------------

CURRENT_TEST="gitignore: env files and backups are ignored"
for path in .env .env.prod .env.bak-20260930 backend/.env \
  backend/.env.bak-20260820-213431 backend/.env.bak-test backend/.env.local; do
  if ! git -C "${REPO_DIR}" check-ignore -q --no-index -- "${path}"; then
    fail "${path} is not ignored"
  fi
done

CURRENT_TEST="gitignore: templates stay trackable"
for path in .env.prod.example backend/.env.example; do
  if git -C "${REPO_DIR}" check-ignore -q --no-index -- "${path}"; then
    fail "${path} is ignored, but it is a tracked template"
  fi
done

# --- 2. .dockerignore -------------------------------------------------------

docker_ok=0
if command -v docker > /dev/null 2>&1 && docker buildx version > /dev/null 2>&1 \
  && docker info > /dev/null 2>&1; then
  docker_ok=1
fi

if [[ "${docker_ok}" -eq 0 ]]; then
  if [[ "${SECRET_HYGIENE_DOCKER:-0}" == "1" ]]; then
    echo "FAIL: docker with buildx is required (SECRET_HYGIENE_DOCKER=1)." >&2
    exit 1
  fi
  echo "SKIP: .dockerignore checks (no docker with buildx available)."
else
  # Builds context dir $1 with a FROM-scratch Dockerfile that copies the whole
  # context, and prints the relative paths that ended up in the result.
  copied_paths() {
    local ctx="$1" out="${TMP_ROOT}/out-$(basename "$1")"
    printf 'FROM scratch\nCOPY . /ctx/\n' > "${TMP_ROOT}/Dockerfile.probe"
    docker buildx build --quiet -f "${TMP_ROOT}/Dockerfile.probe" \
      --output "type=local,dest=${out}" "${ctx}" > /dev/null
    (cd "${out}/ctx" && find . -mindepth 1 | sed 's|^\./||' | sort)
  }

  assert_in() { grep -qxF -- "$1" <<< "$2" || fail "expected '$1' in the build context"; }
  assert_out() { ! grep -qxF -- "$1" <<< "$2" || fail "'$1' must not reach the build context"; }

  CURRENT_TEST="backend/.dockerignore: no secrets, tests or data in the image"
  ctx="${TMP_ROOT}/backend"
  mkdir -p "${ctx}/app/sub/__pycache__" "${ctx}/tests" "${ctx}/data" "${ctx}/alembic"
  cp "${REPO_DIR}/backend/.dockerignore" "${ctx}/.dockerignore"
  for f in .env .env.bak-20260820-213431 .env.bak-20260930215235 .env.local \
    .env.example app/main.py app/.env app/sub/__pycache__/x.cpython-312.pyc \
    tests/test_x.py data/photo.jpg alembic/env.py alembic.ini pyproject.toml; do
    printf 'x\n' > "${ctx}/${f}"
  done
  paths="$(copied_paths "${ctx}")"
  for p in .env .env.bak-20260820-213431 .env.bak-20260930215235 .env.local \
    app/.env tests data app/sub/__pycache__; do
    assert_out "${p}" "${paths}"
  done
  for p in .env.example app/main.py alembic/env.py alembic.ini pyproject.toml; do
    assert_in "${p}" "${paths}"
  done

  CURRENT_TEST="root .dockerignore: no secrets in the frontend context"
  ctx="${TMP_ROOT}/root"
  mkdir -p "${ctx}/backend" "${ctx}/frontend/src" "${ctx}/frontend/node_modules/x"
  cp "${REPO_DIR}/.dockerignore" "${ctx}/.dockerignore"
  for f in .env.prod .env.prod.example backend/.env backend/.env.bak-1 \
    backend/.env.example frontend/package.json frontend/src/main.jsx \
    frontend/node_modules/x/index.js; do
    printf 'x\n' > "${ctx}/${f}"
  done
  paths="$(copied_paths "${ctx}")"
  for p in .env.prod backend/.env backend/.env.bak-1 frontend/node_modules; do
    assert_out "${p}" "${paths}"
  done
  for p in .env.prod.example backend/.env.example frontend/package.json frontend/src/main.jsx; do
    assert_in "${p}" "${paths}"
  done
fi

# --- Result -----------------------------------------------------------------

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "${FAILURES} assertion(s) failed." >&2
  exit 1
fi
echo "All secret hygiene tests passed."
