#!/usr/bin/env bash
# E1: salience gate off vs on, companion S/M/L x3 each, on a LOCAL stack
# (docker postgres/redis + cloud.api on the host), never on production.
# The two arms run side by side on two API ports; adds inside a run are
# sequential (one day at a time), as an app would send them.
#   E1_ENV      file with OPENAI_API_KEY / COHERE_API_KEY / MENGRAM_API_KEY
#   E1_LAUNCHER python launcher that starts cloud.api: <launcher> <gate 0|1> <port>
set -u
: "${E1_ENV:?}" "${E1_LAUNCHER:?}"
set -a; . "$E1_ENV"; set +a
pkill -f "run_local_api.py" 2>/dev/null; sleep 2
for gate in 0 1; do
  port=$((8420 + gate))
  nohup python3 "$E1_LAUNCHER" "$gate" "$port" > "results/e1-api-gate$gate.log" 2>&1 &
done
for port in 8420 8421; do
  for i in $(seq 1 30); do curl -sf "localhost:$port/health" >/dev/null && break; sleep 1; done
done
echo "apis up $(date +%H:%M:%S)"
arm_loop() {  # $1 = arm name, $2 = port
  for run in 1 2 3; do
    for scale in S M L; do
      name="e1-$1-${run}"
      [ -f "results/${name}-mengram-companion-${scale}/result.json" ] && continue
      echo "=== $name companion $scale $(date +%H:%M:%S)"
      MENGRAM_BASE_URL="http://localhost:$2" python3 bench_memory.py run --system mengram --type companion --scale "$scale" --run "$name" 2>&1 | tail -1
    done
  done
}
arm_loop off 8420 > results/e1-off.log 2>&1 &
arm_loop on 8421 > results/e1-on.log 2>&1 &
wait
cat results/e1-off.log results/e1-on.log
echo "=== e1 done $(date +%H:%M:%S)"
