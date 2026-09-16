#!/usr/bin/env bash
# E2c: the E2b runs the sleeping Mac invalidated, re-done under caffeinate.
set -u
: "${E1_ENV:?}"
set -a; . "$E1_ENV"; set +a
run_one() {  # type scale port
  [ -f "results/e2c-mengram-${1}-${2}/result.json" ] && return
  echo "=== e2c mengram $1 $2 $(date +%H:%M:%S)"
  MENGRAM_BASE_URL="http://localhost:$3" python3 bench_memory.py run --system mengram --type "$1" --scale "$2" --run e2c 2>&1 | tail -1
}
( run_one coding M 8420; run_one coding L 8420; run_one companion S 8420 ) > results/e2c-a.log 2>&1 &
( run_one support L 8421; run_one companion M 8421; run_one companion L 8421 ) > results/e2c-b.log 2>&1 &
wait
echo "=== e2c done $(date +%H:%M:%S)"
