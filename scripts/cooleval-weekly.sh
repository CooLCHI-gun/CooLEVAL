#!/usr/bin/env bash
#
# cooleval-weekly.sh — OOM-guarded weekly feedback-loop driver.
#
# Runs the CooLEVAL measurement pipeline (ETL -> metrics -> report) as a
# short-lived, memory-capped, non-concurrent job. Intended to be invoked by a
# scheduler (e.g. Hermes cron with no_agent=True) so NO full agent/LLM runtime
# is spawned. Pure SQLite reads/writes; peak RSS measured ~21-22 MB per step.
#
# OOM-AVERSE GUARDS (per Sonar review, 2026-08-20):
#   1. Single-job lock  — never run two instances concurrently.
#   2. Memory pre-check — skip + log if free is too tight or swap is thrashing.
#   3. ulimit -v cap    — a runaway process dies by itself, gateway survives.
#   4. nice/ionice      — don't starve the always-on gateway of CPU/IO.
#
# Usage:  bash scripts/cooleval-weekly.sh          # from repo root
# Env:    EVAL_DB / EVAL_OUT optional overrides (same as the eval-* scripts).
#
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="${LOCK:-/tmp/cooleval-weekly.lock}"
LOG="${LOG:-${ROOT}/reports/cooleval-weekly.log}"

# Minimum available memory (MB) before we will run.
MIN_AVAIL_MB="${MIN_AVAIL_MB:-600}"
# Maximum swap in use (MB) — above this we assume the box is already thrashing.
MAX_SWAP_USED_MB="${MAX_SWAP_USED_MB:-2500}"
# Virtual-memory cap for the whole pipeline (KB). Belt-and-suspenders:
# real peak RSS is ~22 MB, this is a hard ceiling that must never be reached.
ULIMIT_V_KB="${ULIMIT_V_KB:-800000}"

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date '+%F %T')  $*" >> "$LOG"; echo "$(date '+%F %T')  $*"; }

# --- 1. single-job lock -----------------------------------------------------
if ! mkdir "$LOCK" 2>/dev/null; then
  log "SKIP: another cooleval-weekly instance is running ($LOCK exists)."
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# --- 2. memory pre-check ----------------------------------------------------
read_avail_mb() { awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo; }
read_swap_used_mb() { awk '/^SwapTotal/{t=$2} /^SwapFree/{f=$2} END{printf "%d", (t-f)/1024}' /proc/meminfo; }

avail=$(read_avail_mb); swap=$(read_swap_used_mb)
log "memory: available=${avail}MB (min ${MIN_AVAIL_MB}), swap_used=${swap}MB (max ${MAX_SWAP_USED_MB})"
if [ "$avail" -lt "$MIN_AVAIL_MB" ]; then
  log "SKIP: available memory ${avail}MB < ${MIN_AVAIL_MB}MB. Not running to avoid OOM."
  exit 0
fi
if [ "$swap" -gt "$MAX_SWAP_USED_MB" ]; then
  log "SKIP: swap usage ${swap}MB > ${MAX_SWAP_USED_MB}MB (box likely thrashing). Not running."
  exit 0
fi

# --- 3. memory cap + low priority ------------------------------------------
ulimit -v "$ULIMIT_V_KB" 2>/dev/null
PRIO=(nice -n 10)
[ -n "$(command -v ionice)" ] && PRIO=(nice -n 10 ionice -c2 -n7)

# --- 4. run pipeline --------------------------------------------------------
cd "$ROOT" || exit 1
rc=0
for step in eval-etl.py eval-metrics.py eval-report.py; do
  start=$(date +%s)
  "${PRIO[@]}" /usr/bin/env python3 "scripts/$step" >> "$LOG" 2>&1
  code=$?
  dur=$(( $(date +%s) - start ))
  log "step ${step}: exit=${code} (${dur}s)"
  [ "$code" -ne 0 ] && rc=$code && break
done

log "cooleval-weekly done (rc=${rc})."
exit "$rc"
