#!/usr/bin/env bash
#
# Tests for scripts/victus-ram-report.sh.
#
# `ps`, `free` and `docker` are stubs answering from a fixed process table, a
# fixed `free -m` and a fixed `docker stats` output. A fake /proc (PROC_ROOT)
# holds cmdline files and cwd links for some processes; a fake
# ~/.claude/projects (CLAUDE_PROJECTS_DIR) holds session logs for the cwd
# fallback. No real processes are inspected and nothing is signalled.
#
# Usage: bash tests/victus-ram-report.test.sh

set -Eeuo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${TESTS_DIR}/.." && pwd)"
SCRIPT="${REPO_DIR}/scripts/victus-ram-report.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

FAILURES=0
CURRENT_TEST=""
SID_A="11111111-2222-3333-4444-555555555555"
SID_B="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SID_C="99999999-8888-7777-6666-555555555555"
AGENT="agent-1793629186996-s0f3p1"

fail() {
  echo "FAIL [${CURRENT_TEST}]: $1" >&2
  FAILURES=$((FAILURES + 1))
}

# Process table: pid ppid etimes rss(KiB) comm
#   100 claude, 25 h old, 400 MB, child 101 (100 MB) with grandchild 102 (50 MB);
#       argv and cwd from the fake /proc
#   200 claude, 2 h 46 m old, 200 MB, no /proc entry: argv from `ps -o args=`,
#       cwd from the session log
#   300 claude, 50 min old, 300 MB: below the default age limit
#   600 claude, 5 h 33 m old, 100 MB, malicious session id, no cwd anywhere
#   700 claude, 4 h 10 m old, 100 MB, cwd from a session log with a Windows path
#   400/401 "Web Content" (comm with a space), 1 GiB each
#   500 kernel thread with rss 0 (ignored)
write_fixtures() {
  cat > "${SANDBOX}/ps.table" <<'EOF'
    1     0 100000   15360 systemd
  100     1  90000  409600 claude
  101   100  90000  102400 MainThread
  102   101  90000   51200 node
  200     1  10000  204800 claude
  300     1   3000  307200 claude
  400     1  80000 1048576 Web Content
  401     1  80000 1048576 Web Content
  500     2  50000       0 kworker/0:1
  600     1  20000  102400 claude
  700     1  15000  102400 claude
EOF

  mkdir -p "${SANDBOX}/proc/100" "${SANDBOX}/proc/600" "${SANDBOX}/proc/700" "${SANDBOX}/home"
  printf '%s\0' claude --dangerously-skip-permissions --model 'opus[1m]' \
    --mcp-config "/home/x/.agentsroom/env/${AGENT}.abc123.mcp.json" \
    --session-id "${SID_A}" --system-prompt 'long prompt with spaces --resume nope' \
    > "${SANDBOX}/proc/100/cmdline"
  ln -s "${SANDBOX}/home/projekte/privat/Lego-Arbitrage/.worktrees/some-branch" "${SANDBOX}/proc/100/cwd"
  printf '%s\0' claude --model haiku --session-id '*' > "${SANDBOX}/proc/600/cmdline"
  printf '%s\0' claude --model=fable "--resume=${SID_C}" > "${SANDBOX}/proc/700/cmdline"
  echo "claude --model sonnet[1m] --resume ${SID_B} --append-system-prompt-file /x" > "${SANDBOX}/args.200"

  mkdir -p "${SANDBOX}/projects/-srv-other" "${SANDBOX}/projects/-win"
  printf '{"type":"x"}\n{"cwd":"/srv/other","sessionId":"%s"}\n{"cwd":"/later"}\n' "${SID_B}" \
    > "${SANDBOX}/projects/-srv-other/${SID_B}.jsonl"
  printf '{"cwd":"F:\\\\-=Projekte\\\\Conuti"}\n' > "${SANDBOX}/projects/-win/${SID_C}.jsonl"
  # A session log named "*.jsonl" must never be picked up for the bad id.
  printf '{"cwd":"/glob/injection"}\n' > "${SANDBOX}/projects/-win/zzzz.jsonl"

  printf 'lego-api-prod\t53.59MiB / 30.99GiB\nlego-worker-prod\t1.5GiB / 30.99GiB\nopen-webui\t512KiB / 1GiB\ntiny\t2048B / 64MiB\n' \
    > "${SANDBOX}/docker.stats"
}

make_sandbox() {
  CURRENT_TEST="$1"
  SANDBOX="${TMP_ROOT}/$1"
  mkdir -p "${SANDBOX}/bin"
  export SANDBOX DOCKER_FAIL=0 MEM_AVAIL=3000
  : > "${SANDBOX}/docker.log"
  write_fixtures

  cat > "${SANDBOX}/bin/ps" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "-eo" ]]; then cat "${SANDBOX}/ps.table"; exit 0; fi
if [[ "$1" == "-o" && "$2" == "args=" && "$3" == "-p" ]]; then
  [[ -f "${SANDBOX}/args.$4" ]] && cat "${SANDBOX}/args.$4" && exit 0
  exit 1
fi
echo "unexpected ps call: $*" >&2
exit 99
STUB
  cat > "${SANDBOX}/bin/free" <<'STUB'
#!/usr/bin/env bash
cat <<EOF
               total        used        free      shared  buff/cache   available
Mem:           31729       25058         447        1091        7944        ${MEM_AVAIL}
Swap:          32248        9150       23098
EOF
STUB
  cat > "${SANDBOX}/bin/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SANDBOX}/docker.log"
[[ "${DOCKER_FAIL}" == "1" ]] && exit 1
[[ "$1" == "stats" ]] && cat "${SANDBOX}/docker.stats"
STUB
  chmod +x "${SANDBOX}/bin/"*
}

# Runs the report; sets RC and OUT.
run_report() {
  set +e
  OUT="$(PATH="${SANDBOX}/bin:${PATH}" HOME="${SANDBOX}/home" \
    PROC_ROOT="${SANDBOX}/proc" CLAUDE_PROJECTS_DIR="${SANDBOX}/projects" \
    bash "${SCRIPT}" "$@" 2>&1)"
  RC=$?
  set -e
  printf '%s\n' "${OUT}" > "${SANDBOX}/out.log"
}

expect_rc() { [[ "${RC}" == "$1" ]] || fail "expected exit $1, got ${RC}"; }
expect_line() {
  grep -Eq -- "$1" <<<"${OUT}" || fail "missing line /$1/ in output (see ${SANDBOX}/out.log)"
}
expect_no_line() {
  if grep -Eq -- "$1" <<<"${OUT}"; then fail "unexpected line /$1/ in output"; fi
}
# Row of the claude table for a pid.
row() { grep -E "^$1 " <<<"${OUT}" || true; }

# ── 1. Full report with defaults ──
make_sandbox defaults
run_report
expect_rc 0
expect_line '^RAM verfuegbar +3000 MB$'
expect_line '^Swap belegt +9150 / 32248 MB$'
expect_line 'RAM-Guard \(6144 MB\): Autopilot wuerde jetzt AUSLASSEN \(fehlen 3144 MB\)'
# Grouping by name, including a name with a space; rss 0 is ignored.
expect_line '^claude +5 +1100 '
expect_line '^Web Content +2 +2048 '
expect_line '^MainThread +1 +100 '
expect_no_line '^kworker'
# Claude totals: all five vs. the four older than 2 h (100, 200, 600, 700).
expect_line '^claude gesamt: 5 Prozesse, 1100 MB RSS, 1250 MB inkl. Kindprozesse$'
expect_line '^davon aelter als 2 h: 4 Prozesse, 800 MB RSS, 950 MB inkl. Kindprozesse$'
[[ -z "$(row 300)" ]] || fail "young claude 300 must not be listed"
# Row 100: argv and cwd from /proc, children summed, home shortened.
r="$(row 100)"
[[ "${r}" =~ ^100\ +1d\ 01h\ +400\ +550\ +opus\[1m\]\ +${AGENT}\ +${SID_A}\ +~/projekte/privat/Lego-Arbitrage/.worktrees/some-branch$ ]] ||
  fail "row 100 wrong: ${r}"
# Row 200: argv via `ps -o args=`, cwd from the FIRST cwd in the session log.
r="$(row 200)"
[[ "${r}" =~ ^200\ +2h\ 46m\ +200\ +200\ +sonnet\[1m\]\ +-\ +${SID_B}\ +/srv/other$ ]] || fail "row 200 wrong: ${r}"
# Row 600: a non-uuid session id is dropped, cwd unknown, no glob match.
r="$(row 600)"
[[ "${r}" =~ ^600\ +5h\ 33m\ +100\ +100\ +haiku\ +-\ +-\ +\?$ ]] || fail "row 600 wrong: ${r}"
expect_no_line 'glob/injection'
# Row 700: --flag=value forms, JSON-escaped Windows path unescaped.
r="$(row 700)"
[[ "${r}" == *"fable"*"${SID_C}"*'F:\-=Projekte\Conuti' ]] || fail "row 700 wrong: ${r}"
# Oldest first.
order="$(grep -Eo '^(100|200|600|700) ' <<<"${OUT}" | tr -d ' ' | paste -sd, -)"
[[ "${order}" == "100,600,700,200" ]] || fail "rows not sorted by age: ${order}"
# Summary per directory: the worktree counts towards its repository.
expect_line '^ +550 MB +1  ~/projekte/privat/Lego-Arbitrage$'
# Docker: units B/KiB/MiB/GiB summed, lego- share, largest first.
expect_line '^Container: 4, Speicher zusammen: 1590 MB$'
expect_line '^davon lego-\*: 2 Container, 1590 MB$'
first_container="$(grep -A1 '^Groesste Container:' <<<"${OUT}" | tail -n1)"
[[ "${first_container}" == *lego-worker-prod*1536\ MB ]] || fail "largest container wrong: ${first_container}"
[[ "$(cat "${SANDBOX}/docker.log")" == "stats --no-stream --format {{.Name}}\t{{.MemUsage}}" ]] ||
  fail "docker called with something other than one 'stats': $(cat "${SANDBOX}/docker.log")"

# ── 2. Enough RAM, lower age limit, no docker ──
make_sandbox enough-ram
export MEM_AVAIL=8000
run_report --min-age-hours 0 --no-docker
expect_rc 0
expect_line 'RAM-Guard \(6144 MB\): ok \(1856 MB Luft\)'
expect_line '^davon aelter als 0 h: 5 Prozesse, 1100 MB RSS'
[[ -n "$(row 300)" ]] || fail "--min-age-hours 0 must list claude 300"
expect_no_line '^== Docker =='
[[ ! -s "${SANDBOX}/docker.log" ]] || fail "--no-docker still called docker"

# ── 3. --top limits the name table and sums the rest ──
make_sandbox top
run_report --top 1 --no-docker
expect_rc 0
expect_line '^Web Content +2 +2048 '
expect_line '^\(uebrige 4 Namen\) +8 +1265 '
expect_no_line '^claude +5 '

# ── 4. Docker unreachable: report still completes ──
make_sandbox docker-down
export DOCKER_FAIL=1
run_report
expect_rc 0
expect_line '^Docker nicht erreichbar'
expect_line '^Nur lesend'

# ── 5. Usage errors ──
make_sandbox usage
run_report --top 0
expect_rc 2
run_report --min-age-hours abc
expect_rc 2
run_report --bogus
expect_rc 2
run_report --help
expect_rc 0
expect_line '^Usage: scripts/victus-ram-report.sh'

# ── 6. Read-only by construction: no signalling, no privilege escalation ──
CURRENT_TEST="read-only"
if grep -vE '^\s*#' "${SCRIPT}" | grep -Eq '\b(kill|pkill|killall|sudo|doas|renice)\b|docker +(stop|kill|rm|restart|update)'; then
  fail "script contains a process-changing command"
fi

if ((FAILURES > 0)); then
  echo "${FAILURES} failure(s)" >&2
  exit 1
fi
echo "victus-ram-report: all tests passed"
