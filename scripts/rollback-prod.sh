#!/usr/bin/env bash
#
# Roll the LEGO-Arbitrage production stack on victus back to an earlier commit.
#
# Usage: scripts/rollback-prod.sh [--dry-run] <commit>
#
# Steps (each one printed; with --dry-run the changing steps are only shown):
#   1. take the deploy lock (same lock as scripts/deploy-prod.sh)
#   2. resolve <commit> (after `git fetch origin`), refuse a dirty checkout
#   3. compare the Alembic revisions of <commit> with the revision stored in
#      the database. Migrations are NEVER rolled back; the script only warns
#      when the database is ahead of (or behind) the target commit.
#   4. `git checkout --detach <commit>`
#   5. build the images with GIT_SHA=<commit> while the old containers serve;
#      a failed build restores the previous checkout and leaves the containers
#      untouched
#   6. `docker compose up -d` for postgres redis api worker beat frontend
#   7. scripts/health-check-prod.sh --expect-sha <commit> --wait
#
# Afterwards the checkout is on a detached HEAD, so scripts/deploy-prod.sh
# refuses to run until `git checkout main` (see docs/deploy.md#rollback).
#
# Exit codes: 0 rolled back and healthy (or dry run passed its checks),
#             1 rollback or health check failed, 2 usage/precondition error.
#
# Honoured variables:
#   ENV_FILE          default .env.prod
#   COMPOSE_FILE      default docker-compose.prod.yml
#   COMPOSE_OVERRIDE  optional second compose file (test stacks)
#   COMPOSE_PROJECT   compose project; default lego-arbitrage (prod). Passed
#                     to compose as -p only when set explicitly.
#   LOCK_FILE         default /tmp/lego-arbitrage-deploy.lock
#   DEPLOY_LOCK_HELD  1 = the caller (deploy-prod.sh --auto-rollback) already
#                     holds the deploy lock
#   HEALTH_WAIT       seconds the health check may take (default 180)
#   SKIP_FETCH        1 = do not `git fetch origin` before resolving <commit>
#   REPO_DIR          checkout to roll back (default: the one holding this script)
#   plus everything scripts/health-check-prod.sh honours (HEALTH_URL, ...)

set -Eeuo pipefail

# The target commit usually predates this script, so `git checkout` would
# replace (or delete) the very files that are running. Run from a private copy
# of this script and the health check instead.
if [[ -z "${ROLLBACK_PRIVATE_COPY:-}" ]]; then
  _src_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  _copy_dir="$(mktemp -d "${TMPDIR:-/tmp}/lego-rollback.XXXXXX")"
  cp "${_src_dir}/rollback-prod.sh" "${_copy_dir}/rollback-prod.sh"
  if [[ -f "${_src_dir}/health-check-prod.sh" ]]; then
    cp "${_src_dir}/health-check-prod.sh" "${_copy_dir}/health-check-prod.sh"
  fi
  export ROLLBACK_PRIVATE_COPY="${_copy_dir}"
  export REPO_DIR="${REPO_DIR:-$(cd -- "${_src_dir}/.." && pwd)}"
  exec bash "${_copy_dir}/rollback-prod.sh" "$@"
fi

main() {
  local copy_dir="${ROLLBACK_PRIVATE_COPY}"
  trap 'rm -rf "${ROLLBACK_PRIVATE_COPY}"' EXIT

  local dry_run=0 target=""
  while (( $# > 0 )); do
    case "$1" in
      --dry-run|-n) dry_run=1; shift ;;
      -h|--help) usage ;;
      -*) echo "Unknown option: $1" >&2; usage ;;
      *)
        [[ -z "${target}" ]] || { echo "Only one commit allowed." >&2; usage; }
        target="$1"
        shift
        ;;
    esac
  done
  [[ -n "${target}" ]] || usage

  ENV_FILE="${ENV_FILE:-.env.prod}"
  COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
  COMPOSE_OVERRIDE="${COMPOSE_OVERRIDE:-}"
  LOCK_FILE="${LOCK_FILE:-/tmp/lego-arbitrage-deploy.lock}"
  HEALTH_WAIT="${HEALTH_WAIT:-180}"
  DRY_RUN="${dry_run}"
  local health_script="${copy_dir}/health-check-prod.sh"

  cd "${REPO_DIR}"
  local t0
  t0="$(date +%s)"
  say "Rollback of ${REPO_DIR} to '${target}'$( (( DRY_RUN )) && echo ' (DRY RUN: nothing is changed)')"

  # ── 1. lock ──────────────────────────────────────────
  if [[ "${DEPLOY_LOCK_HELD:-0}" == "1" ]]; then
    say "Deploy lock already held by the caller."
  elif command -v flock >/dev/null 2>&1; then
    exec 9>"${LOCK_FILE}"
    if ! flock -n 9; then
      echo "Another deploy or rollback is running (lock: ${LOCK_FILE}). Aborting." >&2
      exit 2
    fi
  else
    echo "flock not found — proceeding without the deploy lock." >&2
  fi

  # ── 2. preconditions ─────────────────────────────────
  [[ -f "${ENV_FILE}" ]] || { echo "Missing ${ENV_FILE} in ${REPO_DIR}" >&2; exit 2; }
  [[ -f "backend/.env" ]] || { echo "Missing backend/.env in ${REPO_DIR}" >&2; exit 2; }
  [[ -f "${health_script}" ]] || { echo "scripts/health-check-prod.sh missing next to this script" >&2; exit 2; }

  if [[ "${SKIP_FETCH:-0}" != "1" ]]; then
    git fetch --quiet origin || echo "WARN: git fetch origin failed; resolving '${target}' from local objects only." >&2
  fi
  local target_sha
  if ! target_sha="$(git rev-parse --verify --quiet "${target}^{commit}")" || [[ -z "${target_sha}" ]]; then
    echo "Unknown commit '${target}'. Aborting." >&2
    exit 2
  fi
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "The checkout has local changes to tracked files; refusing to check out ${target_sha}." >&2
    git status --short >&2
    exit 2
  fi

  local prev_sha prev_ref
  prev_sha="$(git rev-parse HEAD)"
  prev_ref="$(git branch --show-current)"
  prev_ref="${prev_ref:-${prev_sha}}"
  say "Current checkout: ${prev_ref} at $(git log -1 --format='%h %s' "${prev_sha}")"
  say "Target commit:    $(git log -1 --format='%h %s (%ci)' "${target_sha}")"
  if [[ "${target_sha}" == "${prev_sha}" ]]; then
    say "Target equals the current checkout; the images are rebuilt and the containers recreated anyway."
  elif ! git merge-base --is-ancestor "${target_sha}" "${prev_sha}"; then
    say "WARN: ${target_sha:0:12} is not an ancestor of the current checkout (not a step back on this line)."
  fi

  # ── 3. schema check (warn only) ──────────────────────
  check_schema "${target_sha}"

  # ── 4.-6. checkout, build, swap ──────────────────────
  export GIT_SHA="${target_sha}"
  run git checkout --quiet --detach "${target_sha}"

  if ! run compose build; then
    echo "Build of ${target_sha} failed; the running containers were NOT touched. Restoring checkout ${prev_ref}." >&2
    git checkout --quiet "${prev_ref}" || echo "Could not restore checkout ${prev_ref}; fix it by hand." >&2
    exit 1
  fi
  run compose up -d --remove-orphans postgres redis api worker beat frontend

  # ── 7. health check ──────────────────────────────────
  local hc_args=(--expect-sha "${target_sha}" --wait "${HEALTH_WAIT}")
  local rc=0
  if (( DRY_RUN )); then
    say "[dry-run] would run: health-check-prod.sh ${hc_args[*]}"
    say "Current state of the stack (informational, version check against the running commit skipped):"
    bash "${health_script}" --no-version-check || true
    say "Dry run finished after $(( $(date +%s) - t0 ))s. Nothing was changed."
    exit 0
  fi

  bash "${health_script}" "${hc_args[@]}" || rc=$?
  local elapsed=$(( $(date +%s) - t0 ))
  if (( rc != 0 )); then
    echo "Rollback to ${target_sha} FAILED its health check after ${elapsed}s. Containers run ${target_sha:0:12}; checkout stays detached." >&2
    compose logs --tail=60 api worker >&2 || true
    exit 1
  fi
  say "Rollback to ${target_sha} finished after ${elapsed}s; health check passed."
  say "The checkout is on a detached HEAD. Once the fix or revert is on main: git checkout main && bash scripts/deploy-prod.sh"
}

usage() {
  sed -n '/^# Usage:/,/^# Exit codes/p' "${ROLLBACK_PRIVATE_COPY}/rollback-prod.sh" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

say() { echo "[rollback] $*"; }

compose() {
  local args=(--env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")
  [[ -n "${COMPOSE_OVERRIDE}" ]] && args+=(-f "${COMPOSE_OVERRIDE}")
  [[ -n "${COMPOSE_PROJECT:-}" ]] && args+=(-p "${COMPOSE_PROJECT}")
  docker compose "${args[@]}" "$@"
}

# Run a changing step, or only print it in dry-run mode.
run() {
  if (( DRY_RUN )); then
    say "[dry-run] would run: $*"
    return 0
  fi
  say "running: $*"
  "$@"
}

# Alembic revisions of a commit: prints "<revision> <down_revision...>" lines.
alembic_graph() {
  local sha="$1" file content rev downs
  while IFS= read -r file; do
    [[ "${file}" == *.py ]] || continue
    content="$(git show "${sha}:${file}")"
    rev="$(sed -n -E 's/^revision[^=]*=[[:space:]]*["'\'']([0-9a-zA-Z_]+)["'\''].*/\1/p' <<<"${content}" | head -n1)"
    [[ -n "${rev}" ]] || continue
    downs="$(grep -E '^down_revision' <<<"${content}" | grep -oE '["'\''][0-9a-zA-Z_]+["'\'']' | tr -d '"'\''' | tr '\n' ' ')"
    echo "${rev} ${downs}"
  done < <(git ls-tree -r --name-only "${sha}" -- backend/alembic/versions/)
}

# Current revision(s) in the database, read from the postgres container of the
# stack (works even when the api container is the thing that is broken).
db_revisions() {
  local project="${COMPOSE_PROJECT:-lego-arbitrage}" id
  id="$(docker ps -q \
    --filter "label=com.docker.compose.project=${project}" \
    --filter "label=com.docker.compose.service=postgres" 2>/dev/null | head -n1)" || id=""
  [[ -n "${id}" ]] || return 1
  # shellcheck disable=SC2016  # expanded inside the container
  docker exec "${id}" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"' 2>/dev/null \
    | tr -d '\r' | sed '/^[[:space:]]*$/d'
}

check_schema() {
  local sha="$1" graph db_revs all_revs heads rev line
  graph="$(alembic_graph "${sha}")"
  if [[ -z "${graph}" ]]; then
    say "WARN: no Alembic revisions found in ${sha:0:12}; schema check skipped."
    return
  fi
  all_revs="$(cut -d' ' -f1 <<<"${graph}")"
  heads=""
  for rev in ${all_revs}; do
    if ! cut -d' ' -f2- <<<"${graph}" | tr ' ' '\n' | grep -qx "${rev}"; then
      heads+="${rev} "
    fi
  done
  heads="${heads% }"

  if ! db_revs="$(db_revisions)" || [[ -z "${db_revs}" ]]; then
    say "WARN: could not read alembic_version from the database; schema check skipped (target head: ${heads})."
    return
  fi

  for line in ${db_revs}; do
    if grep -qx "${line}" <<<"${heads// /$'\n'}"; then
      say "Schema: database revision ${line} matches the head of the target commit."
    elif ! grep -qx "${line}" <<<"${all_revs}"; then
      say "WARN: SCHEMA AHEAD — the database is at Alembic revision ${line}, which the target commit does not know (its head: ${heads}). The target's revision is older than the database's."
      say "WARN: Migrations are NOT rolled back. The old code runs against the newer schema; this is safe only if the newer migrations are backward compatible (additive). Otherwise: ticket + deliberate 'alembic downgrade', no further deploys tonight."
    else
      say "WARN: SCHEMA BEHIND — the target commit has migrations after the database revision ${line} (target head: ${heads}). The rollback does not run 'alembic upgrade'; the target code may expect tables or columns that do not exist."
    fi
  done
}

main "$@"
exit $?
