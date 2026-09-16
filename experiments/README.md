# experiments/ — the engineering machine

Every change to how Mengram remembers is measured before it ships, against a
criterion written down *before* the run. This folder is that record.

- `QUEUE.md` — experiments with hypothesis, method and the pre-registered
  pass/fail criterion. `[x]` done, `[-]` rejected, `[~]` running, `[ ]` next.
- `RESULTS.jsonl` — one line per finished experiment: verdict, numbers, what
  was found on the way. Rejected results stay.
- `bench_memory.py` — the ruler. Synthetic dialogues (companion, customer
  support, coding) at 7/30/90 simulated days with facts planted on known days
  and distractors later; questions that only memory can answer. Three numbers
  per system and scale: `recall@old`, `junk_rate`, tokens per question
  (estimate and exact, tiktoken o200k). Systems: `full` (whole history),
  `sandbox` (a reproduction of the OpenAI Agents SDK sandbox memory),
  `mengram` (the API, one throwaway sub-user per run).
- `cost_report.py` — E2: context tokens per request, memory vs full history,
  with the price as a stated input.
- `replay_e1b.py` — E1b: chunk policies replayed on already-built memories.
- `run_*.sh` — the exact invocations behind each RESULTS line.

## Reproduce

```bash
cd experiments
python3 bench_memory.py generate --out corpus --seed 20260916     # corpora + ground truth
OPENAI_API_KEY=... python3 bench_memory.py run --system full --type companion --scale L --run r1
MENGRAM_API_KEY=... OPENAI_API_KEY=... python3 bench_memory.py run --system mengram --type companion --scale L --run r1
python3 bench_memory.py report --dir results
python3 cost_report.py --price-per-m 0.15 --users 1000 --requests-per-user-day 20
```

`MENGRAM_BASE_URL` points `mengram` at a self-hosted instance (`docker-compose.yml`
in the repo root); the numbers in RESULTS.jsonl from E1 on were taken on a
local stack, never on production. A run with failed add jobs is reported as
invalid and not used. Extraction is nondeterministic: repeat-to-repeat spread
on `recall@old` was 0.12–0.17 (E0b), so anything smaller than that is noise,
and claims are made on the mean of three repeats.

## What it found so far (2026-09-16)

| exp | finding | shipped |
|---|---|---|
| E0/E0b | an unnamed speaker's facts landed on a relative's entity; 90-day recall 0.25 → 0.83 | 2.44.8 |
| E1 | a lexical salience gate before write: junk −45% at 30 days, no recall lost in 18 runs | 2.45.0 |
| E1b | 71–78% of injected context tokens were raw chunks; one chunk keeps every answer at −45% tokens | 2.45.1 |
| E2 | memory vs full history: 9–17× fewer context tokens at 30 days, 23–62× at 90; the gate was dropping value-carrying remarks | 2.45.2 |
| E4 (next) | the extractor loses identifiers and options on support dialogue (0.25 vs 0.62) | — |
