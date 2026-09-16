#!/usr/bin/env bash
# E2b: mengram again on all three types after the gate fix E2 found (durable
# markers keep reported/requested facts). Full-history numbers from e2 stand.
set -u
: "${E1_ENV:?}"
set -a; . "$E1_ENV"; set +a
run_one() {  # type scale port
  [ -f "results/e2b-mengram-${1}-${2}/result.json" ] && return
  echo "=== e2b mengram $1 $2 $(date +%H:%M:%S)"
  MENGRAM_BASE_URL="http://localhost:$3" python3 bench_memory.py run --system mengram --type "$1" --scale "$2" --run e2b 2>&1 | tail -1
}
( for sc in S M L; do run_one coding $sc 8420; done; for sc in S M L; do run_one companion $sc 8420; done ) > results/e2b-a.log 2>&1 &
( for sc in S M L; do run_one support $sc 8421; done ) > results/e2b-b.log 2>&1 &
wait
echo "=== e2b done $(date +%H:%M:%S)"
