#!/usr/bin/env bash
#
# Tests for scripts/restore-probe.sh.
#
# Part 1 (always): the pure helpers — row counts from a dump's COPY blocks,
# photo paths from a tar listing, set difference.
# Part 2 (RESTORE_PROBE_DOCKER=1, needs docker and the postgres image): the
# whole probe against a small synthetic dump in a real throwaway container,
# once passing and once with a photo missing from the archive.
#
# Usage: bash tests/restore-probe.test.sh
#        RESTORE_PROBE_DOCKER=1 bash tests/restore-probe.test.sh

set -Eeuo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${TESTS_DIR}/.." && pwd)"
SCRIPT="${REPO_DIR}/scripts/restore-probe.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

FAILURES=0
CURRENT_TEST=""
fail() {
  echo "FAIL [${CURRENT_TEST}]: $1" >&2
  FAILURES=$((FAILURES + 1))
}

# shellcheck source=SCRIPTDIR/../scripts/restore-probe.sh
source "${SCRIPT}"
set +E  # the sourced script sets -E; the helpers are tested without traps

# A plain-format dump the way pg_dump writes it: escaped newline inside a
# value, an empty table, and a value with an escaped backslash before a dot (pg_dump writes \\.).
cat > "${TMP_ROOT}/dump.sql" <<'SQL'
--
-- PostgreSQL database dump
--
SET client_encoding = 'UTF8';
CREATE TABLE public.offers (id integer NOT NULL, offer_title text);
CREATE TABLE public.inventory_photos (id integer NOT NULL, item_id integer NOT NULL, filename text NOT NULL);
CREATE TABLE public.price_records (id integer NOT NULL);
COPY public.offers (id, offer_title) FROM stdin;
1	Millennium Falcon\nOVP
2	Set mit \\. im Titel
3	\N
\.
COPY public.inventory_photos (id, item_id, filename) FROM stdin;
1	7	abc.jpg
2	7	def.png
\.
COPY public.price_records (id) FROM stdin;
\.
--
-- PostgreSQL database dump complete
--
SQL
gzip -c "${TMP_ROOT}/dump.sql" > "${TMP_ROOT}/lego_lego_arbitrage_20261021-033000.sql.gz"
DUMP="${TMP_ROOT}/lego_lego_arbitrage_20261021-033000.sql.gz"

# ── 1. Row counts per COPY block ──
CURRENT_TEST="dump-row-counts"
counts="$(dump_row_counts "${DUMP}")"
expected=$'offers 3\ninventory_photos 2\nprice_records 0'
[[ "${counts}" == "${expected}" ]] || fail "got: ${counts//$'\n'/, }"

# ── 2. Photo paths from a tar listing ──
CURRENT_TEST="photo-paths"
listing=$'media/\nmedia/inventory_photos/\nmedia/inventory_photos/7/\nmedia/inventory_photos/7/abc.jpg\n./media/inventory_photos/8/x.jpg\nmedia/other/7/y.jpg'
paths="$(photo_paths_from_listing <<<"${listing}")"
[[ "${paths}" == $'7/abc.jpg\n8/x.jpg' ]] || fail "got: ${paths//$'\n'/, }"

# ── 3. Set difference ──
CURRENT_TEST="missing-lines"
printf '7/abc.jpg\n7/def.png\n' > "${TMP_ROOT}/want"
printf '7/abc.jpg\n9/zzz.jpg\n' > "${TMP_ROOT}/have"
[[ "$(missing_lines "${TMP_ROOT}/want" "${TMP_ROOT}/have")" == "7/def.png" ]] || fail "only 7/def.png is missing"

# ── 4./5. Full probe in a real container (opt-in) ──
if [[ "${RESTORE_PROBE_DOCKER:-0}" == "1" ]]; then
  make_media() {
    rm -rf "${TMP_ROOT}/m"
    mkdir -p "${TMP_ROOT}/m/media/inventory_photos/7"
    local f
    for f in "$@"; do printf 'x' > "${TMP_ROOT}/m/media/inventory_photos/7/${f}"; done
    tar -C "${TMP_ROOT}/m" -cf "${TMP_ROOT}/lego_media_20261021-033000.tar" media
  }
  run_probe() {
    set +e
    env -u BACKUP_DIR -u DATA_ROOT ENV_FILE=/nonexistent KEY_TABLES="offers inventory_photos" \
      ALERT_CONFIG=/nonexistent \
      bash "${SCRIPT}" --dump "${DUMP}" --no-prod --report "${TMP_ROOT}/report.md" > "${TMP_ROOT}/probe.out" 2>&1
    RC=$?
    set -e
  }

  CURRENT_TEST="probe-pass"
  make_media abc.jpg def.png
  run_probe
  [[ "${RC}" == "0" ]] || fail "probe should pass: $(tail -n 5 "${TMP_ROOT}/probe.out" | tr '\n' ' ')"
  grep -q 'Alle 3 Tabellen: wiederhergestellte Zeilen = Zeilen im Dump (5 Zeilen gesamt)' "${TMP_ROOT}/report.md" \
    || fail "report should confirm the exact row check"
  grep -q 'Alle 2 Foto-Pfade aus inventory_photos liegen im Archiv' "${TMP_ROOT}/report.md" \
    || fail "report should confirm the photo check"

  CURRENT_TEST="probe-missing-photo"
  make_media abc.jpg
  run_probe
  [[ "${RC}" == "1" ]] || fail "probe should fail with a missing photo"
  grep -q '1 von 2 Foto-Pfaden fehlen im Archiv (z. B. 7/def.png' "${TMP_ROOT}/report.md" \
    || fail "report should name the missing photo"
  if docker ps -a --format '{{.Names}}' | grep -q '^lego-restore-probe-'; then
    fail "probe container must be removed"
  fi
else
  echo "restore-probe: container test skipped (set RESTORE_PROBE_DOCKER=1)"
fi

if (( FAILURES > 0 )); then
  echo "${FAILURES} restore-probe test assertion(s) failed" >&2
  exit 1
fi
echo "restore-probe tests passed"
