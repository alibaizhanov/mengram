#!/usr/bin/env bash
# E2: cost report. Mengram (gate on, 1 chunk) on the local stack vs full history,
# all three corpus types, S/M/L, one repeat. Two arms in parallel on two ports.
set -u
: "${E1_ENV:?}"
set -a; . "$E1_ENV"; set +a
run_one() {  # system type scale port
  [ -f "results/e2-${1}-${2}-${3}/result.json" ] && return
  echo "=== e2 $1 $2 $3 $(date +%H:%M:%S)"
  MENGRAM_BASE_URL="http://localhost:$4" python3 bench_memory.py run --system "$1" --type "$2" --scale "$3" --run e2 2>&1 | tail -1
}
( for sc in S M L; do run_one mengram companion $sc 8420; done; for sc in S M L; do run_one mengram support $sc 8420; done ) > results/e2-a.log 2>&1 &
( for sc in S M L; do run_one mengram coding $sc 8421; done; for t in companion support coding; do for sc in S M L; do run_one full $t $sc 8421; done; done ) > results/e2-b.log 2>&1 &
wait
cat results/e2-a.log results/e2-b.log
echo "=== e2 done $(date +%H:%M:%S)"
