#!/usr/bin/env bash
#
# Read-only RAM report for victus (the machine that runs prod and the
# AgentsRoom agents). Answers "why does the autopilot's RAM guard trip?":
#
#   1. Memory overview (`free -m`) and whether the autopilot's RAM guard
#      (available < RAM_GUARD_MB) would skip a run right now.
#   2. RSS per process name (comm), largest first.
#   3. `claude` CLI processes older than --min-age-hours, with their own RSS,
#      RSS including child processes (MCP servers), model, AgentsRoom agent id,
#      Claude session id and working directory.
#   4. Docker: summed container memory (`docker stats`), the lego-*-prod share
#      and the largest containers.
#
# The script never kills, signals or changes anything, and needs no root. The
# working directory of a process comes from /proc/<pid>/cwd when readable;
# otherwise from the "cwd" field of the Claude session log
# (~/.claude/projects/*/<session>.jsonl).
#
# RSS counts shared pages once per process, so the sums are upper bounds.
#
# Usage: scripts/victus-ram-report.sh [--min-age-hours N] [--top N] [--no-docker]
#
# Honoured variables:
#   RAM_GUARD_MB          guard threshold in MB (default 6144, see Memory
#                         autopilot-nachtbetrieb)
#   DOCKER_TIMEOUT        seconds for `docker stats` (default 30)
#   DOCKER_HIGHLIGHT      container name prefix summed separately
#                         (default "lego-")
#   PROC_ROOT             default /proc (tests point it elsewhere)
#   CLAUDE_PROJECTS_DIR   default ${CLAUDE_CONFIG_DIR:-~/.claude}/projects

set -Eeuo pipefail

MIN_AGE_HOURS=2
TOP=15
WITH_DOCKER=1
RAM_GUARD_MB="${RAM_GUARD_MB:-6144}"
DOCKER_TIMEOUT="${DOCKER_TIMEOUT:-30}"
DOCKER_HIGHLIGHT="${DOCKER_HIGHLIGHT:-lego-}"
PROC_ROOT="${PROC_ROOT:-/proc}"
CLAUDE_PROJECTS_DIR="${CLAUDE_PROJECTS_DIR:-${CLAUDE_CONFIG_DIR:-${HOME}/.claude}/projects}"

usage() { sed -n 's/^# \{0,1\}Usage: /Usage: /p' "$0"; }
die() { echo "victus-ram-report: $*" >&2; exit 2; }
is_uint() { [[ "$1" =~ ^[0-9]+$ ]]; }

while (($#)); do
  case "$1" in
    --min-age-hours) [[ $# -ge 2 ]] || die "--min-age-hours needs a value"; MIN_AGE_HOURS="$2"; shift 2 ;;
    --top) [[ $# -ge 2 ]] || die "--top needs a value"; TOP="$2"; shift 2 ;;
    --no-docker) WITH_DOCKER=0; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done
is_uint "${MIN_AGE_HOURS}" || die "--min-age-hours must be a whole number"
{ is_uint "${TOP}" && ((TOP > 0)); } || die "--top must be a positive whole number"
is_uint "${RAM_GUARD_MB}" || die "RAM_GUARD_MB must be a whole number"
is_uint "${DOCKER_TIMEOUT}" || die "DOCKER_TIMEOUT must be a whole number"

MIN_AGE_SECONDS=$((MIN_AGE_HOURS * 3600))

# "3d 04h", "5h 07m", "12m"
fmt_age() {
  local s=$1
  if ((s >= 86400)); then
    printf '%dd %02dh' $((s / 86400)) $((s % 86400 / 3600))
  elif ((s >= 3600)); then
    printf '%dh %02dm' $((s / 3600)) $((s % 3600 / 60))
  else
    printf '%dm' $((s / 60))
  fi
}

short_home() {
  local p=$1
  [[ -n "${HOME:-}" && "${p}" == "${HOME}"* ]] && p="~${p#"${HOME}"}"
  printf '%s' "${p}"
}

# ── 1. Memory overview ──────────────────────────────────────────────
echo "RAM-Report $(hostname 2>/dev/null || echo '?')  $(date '+%Y-%m-%d %H:%M')"
echo

mem_line="$(LC_ALL=C free -m | awk '/^Mem:/{print $2, $3, $7} /^Swap:/{print $2, $3}' | paste -sd' ' -)"
read -r mem_total mem_used mem_avail swap_total swap_used _ <<<"${mem_line} - - - - -"
printf '%-22s %8s MB\n' "RAM gesamt" "${mem_total}" "RAM belegt" "${mem_used}" \
  "RAM verfuegbar" "${mem_avail}" "Swap belegt" "${swap_used} / ${swap_total}"
if is_uint "${mem_avail}"; then
  if ((mem_avail < RAM_GUARD_MB)); then
    echo "RAM-Guard (${RAM_GUARD_MB} MB): Autopilot wuerde jetzt AUSLASSEN (fehlen $((RAM_GUARD_MB - mem_avail)) MB)"
  else
    echo "RAM-Guard (${RAM_GUARD_MB} MB): ok ($((mem_avail - RAM_GUARD_MB)) MB Luft)"
  fi
fi
echo

# One snapshot for everything below: pid ppid etimes rss(KiB) comm (comm last,
# it may contain spaces).
snapshot="$(ps -eo pid=,ppid=,etimes=,rss=,comm=)"

# ── 2. RSS per process name ─────────────────────────────────────────
echo "== RSS je Prozessname (Top ${TOP}) =="
awk -v top="${TOP}" '
  {
    comm = $0
    sub(/^ *[0-9]+ +[0-9]+ +[0-9]+ +[0-9]+ /, "", comm)
    if ($4 <= 0) next
    rss[comm] += $4; cnt[comm]++; total += $4
  }
  END {
    printf "%-24s %7s %10s %7s\n", "Prozess", "Anzahl", "RSS MB", "Anteil"
    n = 0
    for (c in rss) names[++n] = c
    # simple selection sort by rss, descending (n is a few hundred at most)
    for (i = 1; i <= n; i++)
      for (j = i + 1; j <= n; j++)
        if (rss[names[j]] > rss[names[i]]) { t = names[i]; names[i] = names[j]; names[j] = t }
    shown = 0
    for (i = 1; i <= n && i <= top; i++) {
      c = names[i]
      printf "%-24s %7d %10.0f %6.1f%%\n", substr(c, 1, 24), cnt[c], rss[c] / 1024, total ? 100 * rss[c] / total : 0
      shown += rss[c]
    }
    if (n > top) {
      rest_cnt = 0
      for (i = top + 1; i <= n; i++) rest_cnt += cnt[names[i]]
      printf "%-24s %7d %10.0f %6.1f%%\n", "(uebrige " (n - top) " Namen)", rest_cnt, (total - shown) / 1024, total ? 100 * (total - shown) / total : 0
    }
    printf "%-24s %7s %10.0f\n", "Summe", "", total / 1024
  }' <<<"${snapshot}"
echo

# ── 3. Old claude processes ─────────────────────────────────────────
# Emits per claude process: pid etimes rss_kib tree_rss_kib (tree = itself and
# all descendants), oldest first; plus a final "TOTAL count rss tree" line for
# all claude processes regardless of age.
claude_rows="$(awk -v min_age="${MIN_AGE_SECONDS}" '
  {
    comm = $0
    sub(/^ *[0-9]+ +[0-9]+ +[0-9]+ +[0-9]+ /, "", comm)
    pid = $1; ppid[pid] = $2; age[pid] = $3; rss[pid] = $4
    kids[$2] = kids[$2] " " pid
    if (comm == "claude") claude[pid] = 1
  }
  function tree(p,    s, k, n, i) {
    s = rss[p]
    n = split(kids[p], k, " ")
    for (i = 1; i <= n; i++) if (k[i] != "") s += tree(k[i])
    return s
  }
  END {
    for (p in claude) {
      t = tree(p)
      all_n++; all_rss += rss[p]; all_tree += t
      if (age[p] >= min_age) print p, age[p], rss[p], t
    }
    print "TOTAL", all_n + 0, all_rss + 0, all_tree + 0
  }' <<<"${snapshot}")"

read -r _ claude_all claude_all_rss claude_all_tree <<<"$(grep '^TOTAL ' <<<"${claude_rows}")"
old_rows="$(grep -v '^TOTAL ' <<<"${claude_rows}" | sort -k2,2nr || true)"

# Prints "model<TAB>agent<TAB>session" from a process's argv.
claude_argv_info() {
  local pid=$1 model="-" agent="-" session="-" prev="" tok
  local -a argv=()
  if [[ -r "${PROC_ROOT}/${pid}/cmdline" ]]; then
    mapfile -d '' -t argv <"${PROC_ROOT}/${pid}/cmdline" || true
  else
    # Fallback without /proc: whitespace split is good enough for the flags
    # read here (their values carry no spaces).
    read -r -a argv <<<"$(ps -o args= -p "${pid}" 2>/dev/null || true)"
  fi
  for tok in "${argv[@]}"; do
    case "${prev}" in
      --model) model="${tok}" ;;
      --session-id | --resume) session="${tok}" ;;
      --mcp-config)
        if [[ "${tok##*/}" =~ ^(agent-[0-9]+-[a-z0-9]+) ]]; then agent="${BASH_REMATCH[1]}"; fi ;;
    esac
    case "${tok}" in
      --session-id=* | --resume=*) session="${tok#*=}" ;;
      --model=*) model="${tok#*=}" ;;
    esac
    prev="${tok}"
  done
  # Only a plain session id may reach the glob below.
  [[ "${session}" =~ ^[0-9a-fA-F-]+$ ]] || session="-"
  printf '%s\t%s\t%s' "${model}" "${agent}" "${session}"
}

claude_cwd() {
  local pid=$1 session=$2 cwd="" f
  cwd="$(readlink "${PROC_ROOT}/${pid}/cwd" 2>/dev/null || true)"
  if [[ -z "${cwd}" && "${session}" != "-" ]]; then
    for f in "${CLAUDE_PROJECTS_DIR}"/*/"${session}.jsonl"; do
      [[ -r "${f}" ]] || continue
      cwd="$(grep -m1 -o '"cwd":"[^"]*"' "${f}" 2>/dev/null | sed 's/^"cwd":"//; s/"$//; s/\\\\/\\/g' || true)"
      [[ -n "${cwd}" ]] && break
    done
  fi
  if [[ -n "${cwd}" ]]; then short_home "${cwd}"; else printf '?'; fi
}

old_count=0
old_rss=0
old_tree=0
if [[ -n "${old_rows}" ]]; then
  while read -r _ _ rss tree; do
    old_count=$((old_count + 1)); old_rss=$((old_rss + rss)); old_tree=$((old_tree + tree))
  done <<<"${old_rows}"
fi

echo "== claude-Prozesse aelter als ${MIN_AGE_HOURS} h =="
printf 'claude gesamt: %d Prozesse, %d MB RSS, %d MB inkl. Kindprozesse\n' \
  "${claude_all:-0}" $((${claude_all_rss:-0} / 1024)) $((${claude_all_tree:-0} / 1024))
printf 'davon aelter als %d h: %d Prozesse, %d MB RSS, %d MB inkl. Kindprozesse\n' \
  "${MIN_AGE_HOURS}" "${old_count}" $((old_rss / 1024)) $((old_tree / 1024))
if ((old_count > 0)); then
  echo
  printf '%-8s %-8s %7s %8s  %-12s %-28s %-36s %s\n' \
    "PID" "Alter" "RSS MB" "+Kinder" "Modell" "Agent" "Session" "Arbeitsverzeichnis"
  by_dir=""
  while read -r pid etimes rss tree; do
    IFS=$'\t' read -r model agent session <<<"$(claude_argv_info "${pid}")"
    cwd="$(claude_cwd "${pid}" "${session}")"
    printf '%-8s %-8s %7d %8d  %-12s %-28s %-36s %s\n' \
      "${pid}" "$(fmt_age "${etimes}")" $((rss / 1024)) $((tree / 1024)) \
      "${model}" "${agent}" "${session}" "${cwd}"
    # Worktrees count towards their repository.
    by_dir+="${tree}"$'\t'"${cwd%%/.worktrees/*}"$'\n'
  done <<<"${old_rows}"
  echo
  echo "Je Arbeitsverzeichnis (Worktrees beim Repo, inkl. Kindprozesse):"
  printf '%s' "${by_dir}" | awk -F'\t' '
    { n[$2]++; s[$2] += $1 }
    END { for (d in n) printf "%10.0f MB %4d  %s\n", s[d] / 1024, n[d], d }' | sort -k1,1nr
fi
echo

# ── 4. Docker ───────────────────────────────────────────────────────
if ((WITH_DOCKER)); then
  echo "== Docker =="
  docker_out=""
  if command -v docker >/dev/null 2>&1 &&
    docker_out="$(timeout "${DOCKER_TIMEOUT}" docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' 2>/dev/null)"; then
    awk -F'\t' -v hl="${DOCKER_HIGHLIGHT}" '
      function to_mib(v,    n, u) {
        n = v; sub(/[A-Za-z]+$/, "", n); n += 0; u = v; sub(/^[0-9.]+/, "", u)
        if (u == "B") return n / 1048576
        if (u == "KiB" || u == "kB" || u == "KB") return n / 1024
        if (u == "MiB" || u == "MB") return n
        if (u == "GiB" || u == "GB") return n * 1024
        if (u == "TiB" || u == "TB") return n * 1048576
        return 0
      }
      NF >= 2 {
        used = $2; sub(/ *\/.*/, "", used)
        m = to_mib(used)
        name[++n] = $1; mib[n] = m; total += m
        if (hl != "" && index($1, hl) == 1) { hl_n++; hl_sum += m }
      }
      END {
        printf "Container: %d, Speicher zusammen: %.0f MB\n", n, total
        if (hl != "") printf "davon %s*: %d Container, %.0f MB\n", hl, hl_n, hl_sum
        for (i = 1; i <= n; i++)
          for (j = i + 1; j <= n; j++)
            if (mib[j] > mib[i]) { t = mib[i]; mib[i] = mib[j]; mib[j] = t; t = name[i]; name[i] = name[j]; name[j] = t }
        if (n > 0) print "Groesste Container:"
        for (i = 1; i <= n && i <= 5; i++) printf "  %-32s %8.0f MB\n", name[i], mib[i]
      }' <<<"${docker_out}"
  else
    echo "Docker nicht erreichbar (kein docker, keine Rechte oder Timeout ${DOCKER_TIMEOUT} s)"
  fi
  echo
fi

echo "Nur lesend: das Skript beendet keine Prozesse. RSS zaehlt geteilte Seiten je Prozess, Summen sind Obergrenzen."
