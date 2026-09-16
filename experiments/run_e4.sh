#!/usr/bin/env bash
# E4: support S/M/L x3 after the supersede guards + gate request fix; companion L and
# coding L once as the regression check. Local stack, two ports, under caffeinate.
set -u
: "${E1_ENV:?}"
set -a; . "$E1_ENV"; set +a
run_one() {  # run type scale port
  [ -f "results/${1}-mengram-${2}-${3}/result.json" ] && return
  echo "=== $1 mengram $2 $3 $(date +%H:%M:%S)"
  MENGRAM_BASE_URL="http://localhost:$4" python3 bench_memory.py run --system mengram --type "$2" --scale "$3" --run "$1" 2>&1 | tail -1
}
( for r in e4-1 e4-2 e4-3; do for sc in S M; do run_one $r support $sc 8420; done; done; run_one e4-1 coding L 8420 ) > results/e4-a.log 2>&1 &
( for r in e4-1 e4-2 e4-3; do run_one $r support L 8421; done; run_one e4-1 companion L 8421 ) > results/e4-b.log 2>&1 &
wait
echo "=== e4 done $(date +%H:%M:%S)"
