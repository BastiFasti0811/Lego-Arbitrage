#!/usr/bin/env bash
#
# Restore probe for LEGO-Arbitrage backups: proves that a dump from
# backup-db.sh restores and that the photo archive matches it. Production is
# only READ (SELECT count(*) in a read-only transaction); nothing is restored
# into it.
#
# Steps:
#   1. Pick the dump (newest lego_<db>_*.sql.gz in BACKUP_DIR, or --dump) and
#      the photo archive of the same run (lego_media_<same timestamp>.tar,
#      else the newest one, or --media).
#   2. Count the rows of every table straight from the dump's COPY blocks.
#   3. Start a throwaway postgres container (same image as prod, no network,
#      data on tmpfs) and restore the dump with ON_ERROR_STOP.
#   4. Every table: restored rows must equal the dump's rows (exact).
#   5. Key tables (offers, price_records, inventory_items, inventory_photos):
#      compare with prod. Prod keeps growing after the dump, so a small
#      positive drift is expected; an empty restored table while prod has rows
#      fails, more rows restored than prod has warns.
#   6. Every inventory_photos row must resolve to a file in the photo archive
#      (media/inventory_photos/<item_id>/<filename>); the live photo directory
#      is checked too (warning only, a photo may have been deleted since).
#   7. Print a Markdown report, exit 0 (PASS) or 1 (FAIL).
#
# Usage:
#   scripts/restore-probe.sh [--dump FILE] [--media FILE] [--no-media]
#                            [--no-prod] [--report FILE] [--alert]
#
#   --no-media   skip the photo checks (e.g. for a dump without photo archive)
#   --no-prod    skip the comparison with the running prod database
#   --report F   also write the Markdown report to F
#   --alert      send a Telegram alarm when the probe fails (lib/telegram.sh)
#
# Honoured variables (environment, else .env.prod read without sourcing):
#   BACKUP_DIR / DATA_ROOT / POSTGRES_DB   as in backup-db.sh
#   PROD_PROJECT   compose project of prod (default lego-arbitrage)
#   PROBE_IMAGE    postgres image (default: the one in docker-compose.prod.yml)
#   KEY_TABLES     tables compared with prod
#   PROBE_TIMEOUT  seconds to wait for the probe container (default 90)

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=SCRIPTDIR/lib/telegram.sh
source "${SCRIPT_DIR}/lib/telegram.sh"

# ── Pure helpers (sourced by tests/restore-probe.test.sh) ──

# dump_row_counts <dump.sql.gz>: "<table> <rows>" per COPY block. In COPY text
# format every row is one line (newlines inside values are escaped as \n) and
# the block ends with a line that is exactly "\.".
dump_row_counts() {
  gunzip -c "$1" | awk '
    /^COPY / { t = $2; sub(/^public\./, "", t); n = 0; inblk = 1; next }
    inblk && $0 == "\\." { print t, n; inblk = 0; next }
    inblk { n++ }
  '
}

# photo_paths_from_listing: tar listing on stdin -> "<item_id>/<filename>"
# for every file below .../inventory_photos/.
photo_paths_from_listing() {
  sed -n 's#^\(\./\)\{0,1\}[^/]*/inventory_photos/\([^/][^/]*/[^/][^/]*\)$#\2#p'
}

# missing_lines <expected-file> <present-file>: lines of the first file that
# are not in the second.
missing_lines() {
  LC_ALL=C comm -23 <(LC_ALL=C sort -u "$1") <(LC_ALL=C sort -u "$2")
}

probe_cleanup() {
  if [[ -n "${PROBE_CONTAINER:-}" ]]; then
    docker rm -f "${PROBE_CONTAINER}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${PROBE_WORK:-}" ]]; then
    rm -rf "${PROBE_WORK}"
  fi
}

# ── Main ───────────────────────────────────────────────────
main() {
  local dump="" media="" no_media=0 no_prod=0 report_file="" alert=0
  while (( $# > 0 )); do
    case "$1" in
      --dump) dump="$2"; shift 2 ;;
      --media) media="$2"; shift 2 ;;
      --no-media) no_media=1; shift ;;
      --no-prod) no_prod=1; shift ;;
      --report) report_file="$2"; shift 2 ;;
      --alert) alert=1; shift ;;
      -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
      *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
  done

  local env_file="${ENV_FILE:-${REPO_DIR}/.env.prod}"
  env_get() { [[ -r "${env_file}" ]] || return 0; sed -n "s/^$1=//p" "${env_file}" | tail -n1 | tr -d '\r'; }

  local db="${POSTGRES_DB:-$(env_get POSTGRES_DB)}"; db="${db:-lego_arbitrage}"
  local data_root="${DATA_ROOT:-$(env_get DATA_ROOT)}"
  local backup_dir="${BACKUP_DIR:-$(env_get BACKUP_DIR)}"
  [[ -z "${backup_dir}" && -n "${data_root}" ]] && backup_dir="${data_root}/backups"
  local live_media="${BACKUP_MEDIA_DIR:-$(env_get BACKUP_MEDIA_DIR)}"
  [[ -z "${live_media}" && -n "${data_root}" ]] && live_media="${data_root}/media"
  local prod_project="${PROD_PROJECT:-lego-arbitrage}"
  local key_tables="${KEY_TABLES:-offers price_records inventory_items inventory_photos}"
  local timeout="${PROBE_TIMEOUT:-90}"
  local image="${PROBE_IMAGE:-}"
  if [[ -z "${image}" && -r "${REPO_DIR}/docker-compose.prod.yml" ]]; then
    image="$(awk '/^  postgres:/{f=1; next} f && /^  [a-z]/{exit} f && /image:/{print $2; exit}' "${REPO_DIR}/docker-compose.prod.yml")"
  fi
  image="${image:-postgres:16-alpine}"

  ALERT_PROJECT="${ALERT_PROJECT:-${prod_project}}"
  tg_load_config

  # Globals on purpose: the EXIT trap must still see them after `exit`.
  PROBE_WORK="$(mktemp -d)"
  PROBE_CONTAINER=""
  trap probe_cleanup EXIT
  local work="${PROBE_WORK}" status="PASS"

  local report="${work}/report.md"
  : > "${report}"
  out() { printf '%s\n' "$*" | tee -a "${report}"; }
  fail() { status="FAIL"; out "- ❌ $*"; }
  warn() { out "- ⚠️ $*"; }
  ok() { out "- ✅ $*"; }
  finish() {
    out ""
    out "**Ergebnis: ${status}**"
    [[ -n "${report_file}" ]] && cp "${report}" "${report_file}"
    if [[ "${status}" != "PASS" && "${alert}" == "1" ]]; then
      tg_send "🔴 Lego-Restore-Probe ($(hostname 2>/dev/null || echo host)) FEHLGESCHLAGEN für $(basename -- "${dump:-?}"). Bericht: ${report_file:-Konsole}" || true
    fi
    [[ "${status}" == "PASS" ]] && exit 0 || exit 1
  }

  # ── 1. Pick the files ──
  if [[ -z "${dump}" ]]; then
    [[ -n "${backup_dir}" ]] || { echo "No --dump and neither BACKUP_DIR nor DATA_ROOT known" >&2; exit 2; }
    dump="$(compgen -G "${backup_dir}/lego_${db}_*.sql.gz" | sort | tail -n1 || true)"
  fi
  out "# Restore-Probe $(date '+%Y-%m-%d %H:%M %Z')"
  out ""
  out "- Host: $(hostname 2>/dev/null || echo unknown), Image: \`${image}\`, Container ohne Netz, Daten auf tmpfs"
  if [[ -z "${dump}" || ! -f "${dump}" ]]; then
    fail "Kein Dump gefunden (${dump:-${backup_dir}/lego_${db}_*.sql.gz})"
    finish
  fi
  out "- Dump: \`${dump}\` ($(wc -c < "${dump}") Bytes, $(date -r "${dump}" '+%Y-%m-%d %H:%M'))"

  if (( no_media == 0 )) && [[ -z "${media}" ]]; then
    local ts dir same
    ts="$(basename -- "${dump}" .sql.gz)"; ts="${ts##*_}"
    dir="$(dirname -- "${dump}")"
    same="${dir}/lego_media_${ts}.tar"
    if [[ -f "${same}" ]]; then
      media="${same}"
    else
      media="$(compgen -G "${dir}/lego_media_*.tar" | sort | tail -n1 || true)"
    fi
  fi
  if (( no_media == 0 )); then
    if [[ -n "${media}" && -f "${media}" ]]; then
      out "- Foto-Archiv: \`${media}\` ($(wc -c < "${media}") Bytes)"
    else
      out "- Foto-Archiv: keins gefunden"
    fi
  fi
  out ""
  out "## Prüfungen"
  out ""

  if ! gzip -t "${dump}" 2>/dev/null; then
    fail "Dump ist kein gültiges gzip"
    finish
  fi
  if ! gunzip -c "${dump}" | tail -c 200 | grep -q "PostgreSQL database dump complete"; then
    fail "Dump unvollständig (Abschlussmarke fehlt)"
    finish
  fi
  ok "Dump vollständig (gzip ok, Abschlussmarke vorhanden)"

  # ── 2. Expected rows from the dump ──
  dump_row_counts "${dump}" > "${work}/expected"
  local tables
  tables="$(cut -d' ' -f1 "${work}/expected")"
  if [[ -z "${tables}" ]]; then
    fail "Dump enthält keine Tabellendaten"
    finish
  fi
  local t
  for t in ${tables}; do
    [[ "${t}" =~ ^[a-z_][a-z0-9_]*$ ]] || { fail "Unerwarteter Tabellenname im Dump: ${t}"; finish; }
  done

  # ── 3. Throwaway container + restore ──
  PROBE_CONTAINER="lego-restore-probe-$(date +%s)-$$"
  local container="${PROBE_CONTAINER}"
  if ! docker run -d --rm --name "${container}" --network none \
      --tmpfs /var/lib/postgresql/data:rw,size=2g \
      -e POSTGRES_USER=probe -e POSTGRES_DB=probe -e POSTGRES_HOST_AUTH_METHOD=trust \
      "${image}" >/dev/null; then
    PROBE_CONTAINER=""
    fail "Probe-Container ließ sich nicht starten (${image})"
    finish
  fi
  local waited=0
  # The entrypoint runs a temporary server during init; wait for the real one.
  until docker logs "${container}" 2>&1 | grep -q "PostgreSQL init process complete" \
      && docker exec "${container}" pg_isready -q -U probe -d probe >/dev/null 2>&1; do
    if (( waited >= timeout )); then
      fail "Probe-Container nach ${timeout}s nicht bereit"
      finish
    fi
    sleep 1
    waited=$((waited + 1))
  done

  local started=${SECONDS}
  if ! gunzip -c "${dump}" | docker exec -i -e PGOPTIONS='-c client_min_messages=warning' "${container}" \
      psql -X -q -v ON_ERROR_STOP=1 -U probe -d probe > /dev/null 2> "${work}/restore.err"; then
    fail "Restore abgebrochen: $(tail -n 3 "${work}/restore.err" | tr '\n' ' ')"
    finish
  fi
  ok "Restore ohne Fehler in $((SECONDS - started)) s (psql ON_ERROR_STOP)"

  probe_sql() { docker exec "${container}" psql -X -At -F ' ' -U probe -d probe -c "$1"; }
  prod_sql() {
    docker exec -e PGOPTIONS='-c default_transaction_read_only=on' "$1" \
      sh -c 'psql -X -At -F " " -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' _ "$2"
  }
  count_sql() {
    local sep="" s="" name
    for name in "$@"; do
      s+="${sep}select '${name}', count(*) from public.\"${name}\""
      sep=" union all "
    done
    printf '%s' "${s}"
  }

  # ── 4. Exact row check ──
  # shellcheck disable=SC2086  # tables is a whitespace-separated name list
  if ! probe_sql "$(count_sql ${tables})" > "${work}/restored"; then
    fail "Zählabfrage in der Probe-DB fehlgeschlagen"
    finish
  fi
  local mismatches
  mismatches="$(LC_ALL=C join <(LC_ALL=C sort "${work}/expected") <(LC_ALL=C sort "${work}/restored") | awk '$2 != $3 {print $1 ": Dump " $2 ", wiederhergestellt " $3}')"
  if [[ -n "${mismatches}" ]]; then
    fail "Zeilenzahl weicht vom Dump ab: ${mismatches//$'\n'/; }"
  else
    ok "Alle $(wc -l < "${work}/expected") Tabellen: wiederhergestellte Zeilen = Zeilen im Dump ($(awk '{s+=$2} END {print s}' "${work}/expected") Zeilen gesamt)"
  fi

  # ── 5. Key tables vs prod ──
  local prod_pg=""
  if (( no_prod == 0 )); then
    prod_pg="$(docker ps -q --filter "label=com.docker.compose.project=${prod_project}" \
      --filter "label=com.docker.compose.service=postgres" | head -n1)"
    [[ -n "${prod_pg}" ]] || warn "Kein laufender Prod-Postgres (Projekt ${prod_project}); Prod-Vergleich entfällt"
  fi
  local key present_keys=""
  for key in ${key_tables}; do
    if grep -q "^${key} " "${work}/expected"; then
      present_keys+="${key} "
    else
      fail "Schlüsseltabelle ${key} fehlt im Dump"
    fi
  done
  if [[ -n "${prod_pg}" && -n "${present_keys}" ]]; then
    # shellcheck disable=SC2086
    if prod_sql "${prod_pg}" "$(count_sql ${present_keys})" > "${work}/prod"; then
      out ""
      out "| Tabelle | Dump/Probe | Prod jetzt | Differenz |"
      out "|---|---:|---:|---:|"
      local name restored prod_n
      for name in ${present_keys}; do
        restored="$(awk -v t="${name}" '$1 == t {print $2}' "${work}/restored")"
        prod_n="$(awk -v t="${name}" '$1 == t {print $2}' "${work}/prod")"
        out "| ${name} | ${restored:-?} | ${prod_n:-?} | $(( ${prod_n:-0} - ${restored:-0} )) |"
      done
      out ""
      for name in ${present_keys}; do
        restored="$(awk -v t="${name}" '$1 == t {print $2}' "${work}/restored")"
        prod_n="$(awk -v t="${name}" '$1 == t {print $2}' "${work}/prod")"
        if (( ${prod_n:-0} > 0 && ${restored:-0} == 0 )); then
          fail "${name}: Probe leer, Prod hat ${prod_n} Zeilen"
        elif (( ${restored:-0} > ${prod_n:-0} )); then
          warn "${name}: Probe hat mehr Zeilen (${restored}) als Prod (${prod_n}); seit dem Dump wurde gelöscht"
        fi
      done
      ok "Schlüsseltabellen mit Prod verglichen (Differenz = seit dem Dump hinzugekommen)"
    else
      warn "Prod-Zählabfrage fehlgeschlagen; Prod-Vergleich entfällt"
    fi
    local v_probe v_prod
    v_probe="$(probe_sql 'select version_num from alembic_version' 2>/dev/null | tr '\n' ' ' || true)"
    v_prod="$(prod_sql "${prod_pg}" 'select version_num from alembic_version' 2>/dev/null | tr '\n' ' ' || true)"
    if [[ "${v_probe}" == "${v_prod}" ]]; then
      ok "Alembic-Stand gleich (${v_probe% })"
    else
      warn "Alembic-Stand Probe ${v_probe:-?} ≠ Prod ${v_prod:-?} (Migration seit dem Dump; Restore bräuchte alembic upgrade head)"
    fi
  fi

  # ── 6. Photos ──
  if (( no_media == 0 )); then
    if ! grep -q '^inventory_photos ' "${work}/expected"; then
      warn "Dump hat keine Tabelle inventory_photos; Fotoprüfung entfällt"
    elif ! probe_sql "select item_id::text || '/' || filename from inventory_photos" > "${work}/photos"; then
      fail "Foto-Abfrage in der Probe-DB fehlgeschlagen"
    elif [[ -z "${media}" || ! -f "${media}" ]]; then
      fail "Kein Foto-Archiv gefunden: $(wc -l < "${work}/photos") Foto-Zeilen ohne Sicherung"
    elif ! tar -tf "${media}" > "${work}/listing" 2>/dev/null; then
      fail "Foto-Archiv nicht lesbar: ${media}"
    else
      photo_paths_from_listing < "${work}/listing" > "${work}/archived"
      local n_rows n_missing n_orphans missing
      n_rows="$(wc -l < "${work}/photos")"
      missing_lines "${work}/photos" "${work}/archived" > "${work}/missing"
      n_missing="$(wc -l < "${work}/missing")"
      n_orphans="$(missing_lines "${work}/archived" "${work}/photos" | wc -l)"
      if (( n_missing > 0 )); then
        missing="$(head -n 5 "${work}/missing" | tr '\n' ' ')"
        fail "${n_missing} von ${n_rows} Foto-Pfaden fehlen im Archiv (z. B. ${missing})"
      else
        ok "Alle ${n_rows} Foto-Pfade aus inventory_photos liegen im Archiv ($(wc -l < "${work}/archived") Dateien, ${n_orphans} ohne DB-Zeile)"
      fi
      if [[ -n "${live_media}" && -d "${live_media}/inventory_photos" ]]; then
        local p live_missing=0
        while IFS= read -r p; do
          [[ -f "${live_media}/inventory_photos/${p}" ]] || live_missing=$((live_missing + 1))
        done < "${work}/photos"
        if (( live_missing > 0 )); then
          warn "${live_missing} Foto-Pfade aus dem Dump fehlen im Live-Verzeichnis ${live_media}"
        else
          ok "Alle ${n_rows} Foto-Pfade auch im Live-Verzeichnis auflösbar"
        fi
      fi
    fi
  fi

  finish
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
