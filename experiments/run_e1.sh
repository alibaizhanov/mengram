#!/usr/bin/env bash
# E1: salience gate off vs on, companion S/M/L x3 each, on a LOCAL stack
# (docker postgres/redis + cloud.api on the host), never on production.
#   E1_ENV      file with OPENAI_API_KEY / COHERE_API_KEY / MENGRAM_API_KEY / MENGRAM_BASE_URL
#   E1_LAUNCHER python launcher that starts cloud.api: <launcher> <gate 0|1> <port>
set -u
: "${E1_ENV:?}" "${E1_LAUNCHER:?}"
set -a; . "$E1_ENV"; set +a
LOG=${E1_LOG:-results/e1.log}
start_api() {  # $1 = gate
  pkill -f "run_local_api.py" 2>/dev/null; sleep 2
  nohup python3 "$E1_LAUNCHER" "$1" 8420 > "results/e1-api-gate$1.log" 2>&1 &
  for i in $(seq 1 30); do curl -sf localhost:8420/health >/dev/null && break; sleep 1; done
  echo "api up, gate=$1 $(date +%H:%M:%S)"
}
for arm in off on; do
  gate=0; [ "$arm" = on ] && gate=1
  start_api $gate
  for run in 1 2 3; do
    for scale in S M L; do
      name="e1-${arm}-${run}"
      [ -f "results/${name}-mengram-companion-${scale}/result.json" ] && continue
      echo "=== $name companion $scale $(date +%H:%M:%S)"
      python3 bench_memory.py run --system mengram --type companion --scale "$scale" --run "$name" 2>&1 | tail -1
    done
  done
done
echo "=== e1 done $(date +%H:%M:%S)"
