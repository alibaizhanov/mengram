# Engineering Machine — experiment queue (Mengram)
# Carried over from densely/experiments 2026-09-15. Format per Cherny: goal +
# pre-registered verifier, the agent builds the harness. Slope rule per Dean:
# run S/M/L corpus scales, judge the TREND, not one point.
# Statuses: [ ] queued  [~] running  [x] done  [-] rejected (write why)
# Results ledger: RESULTS.jsonl (one line per completed experiment)
# Corpus: experiments/corpus/{companion,support,coding}_{S,M,L}.jsonl
#   S = one week of dialogue, M = one month, L = one quarter. Real data where
#   consented (Ali's own memory + Claude Code sessions), otherwise synthetic
#   with a fixed seed so anyone can regenerate it. Gitignored.
#
# Why this exists (2026-09-15): a memory system's only honest number is
# whether the agent still gets the buried fact a month later, and how much
# junk it stored on the way. Mem0's audit (#4573) found 97.8% junk and a
# model upgrade did not fix it; their published eval could not be reproduced
# (18 votes). Nobody in this market holds a ruler. E0 is the ruler.

## E0 [x] Memory benchmark v0 (harness accepted 2026-09-16; variance rule amended: 3 repeats, report mean and spread) — "does the answer survive?" (the ruler)
Hypothesis: a paired correct/distractor design (from densely/bench_recall.py)
  transfers to memory: the true fact sits in old history, a plausible wrong
  one sits in recent history, the question cannot be answered without recall.
  Scored per case: correct / distractor / other / recalled-at-all.
Method: build experiments/bench_memory.py — `generate` writes N cases per corpus
  type and scale into a fresh Mengram account (or local folder) via `add`, then
  asks via `search`+answer model; `score` compares. Report: recall@old (facts
  older than 30 simulated days found), junk rate (facts stored that no question
  ever needs), tokens per recall. Seeded, public inputs, re-runnable by anyone.
Verify (pre-registered): the harness runs end-to-end on S/M/L for all three
  corpus types and produces the three numbers with a variance < 5% across two
  runs on the same seed. If variance is higher, the ruler is not a ruler —
  fix before any other experiment.
Baselines (added 2026-09-16, after OpenAI's Agents API beta): every run reports
  the same three numbers for at least two baselines next to Mengram —
  (a) "full history": the whole dialogue replayed into context (the upper
      bound on recall, the upper bound on tokens);
  (b) "OpenAI sandbox memory": the Agents SDK `sandbox/memory` behaviour —
      conversation summaries + raw extracts, `memory_summary.md` injected at
      start, keyword search on demand, consolidation that keeps the newest
      raw memories and drops older ones (max_raw_memories_for_consolidation
      = 256). Reproduce it from its open source (see
      growth/openai-agents-harness-2026-09-16.md), do not approximate it.
  The claim we want to be able to make in public, with the harness attached:
  "after 30 simulated days, baseline (b) recalls X% of planted facts, Mengram
  recalls Y%, at Z tokens per request each". If Mengram does not win on
  recall@old at M and L, that is the result and it gets published too.
Progress 2026-09-16: bench_memory.py written (generate/run/report; systems full,
  sandbox, mengram). Corpus generated, seed 20260916: 9 files, 76–730 turns,
  6 cases at S and 8 at M/L (slot pool is 8 per type — extend before claiming
  more). Smoke on mengram/support/S without the answer model: retrieved 5/6,
  580 tokens/question at a 600 budget, stored 19 facts = 5 needed + 7
  distractor + 7 junk (37%). Scoring and the sandbox baseline need
  OPENAI_API_KEY (gpt-4o-mini, T=0). Bench sub-users are named
  bench-<run>-<type>-<scale> in the account; no wipe endpoint yet.

## E1 [ ] Salience gate — a classifier that says "do not store this"
Hypothesis: a small classifier over the fact embedding + cheap features
  (entity is "Assistant", verb is session-chatter, fact length, novelty vs
  existing_context) cuts junk stored by >= 50% while dropping < 5% of facts
  that later answer a benchmark question.
Labels (implicit, already in the database): archived/deleted by a user = junk;
  returned by recall and used in an answer = value; never recalled in 60 days
  = weak junk. Plus Ali's 2026-09-15 feed: 24/100 session-chatter, 6/100 dups.
Method: export labeled facts (aggregated, no emails), train logistic /
  small MLP on Cohere embeddings, evaluate on E0's junk rate and recall@old.
Verify (pre-registered): junk rate on E0 falls by >= 50% AND recall@old falls
  by < 5 points, on M and L scales (slope must hold, not just S). Else reject
  with numbers.

## E2 [ ] Cost report — measure before selling it
Hypothesis: for a builder's account, "tokens of context per request with
  memory" vs "full history per request" differs by >= 5x at M scale and
  >= 10x at L scale, with recall@old within 5 points of full-history.
Method: on E0 corpora, run both strategies (max_tokens budget vs. concatenated
  history) through the same answer model; count tokens (estimate, and tiktoken
  where the model is known); compute $ at a stated price.
Verify (pre-registered): the ratio holds on M and L with recall@old within 5
  points. If recall@old drops more, the budget cut is losing answers and the
  cost report must show that too.

## E3 [ ] Small extractor vs frontier — the CentML bet (gated on E0 + E1)
Hypothesis: a 1–3B model fine-tuned on clean extractions (E1-filtered) matches
  the frontier model on E0 recall@old within 3 points at <= 1/10 the cost.
Method: RunPod (see runpod-setup memory), Qwen-class base, SFT on pairs
  (dialogue chunk → accepted facts); evaluate on E0 M/L.
Verify (pre-registered): recall@old within 3 points AND junk rate not worse
  AND cost per add <= 1/10. Decision date to be set when E0/E1 are done.

## E0b [x] Attribution and paraphrase defects found by E0 (gate for E1)
Result 2026-09-16: recall clause MET — companion/L mean recall@old 0.83 over 3
  repeats (0.75/0.875/0.875), from 0.25-0.50 before the fix; S 0.94, M 0.96.
  Junk clause MISSED by 0.02: L junk mean 0.52 vs 0.50 (paraphrase duplication
  untouched by an attribution fix) — carried into E1 as its baseline. Noise
  floor: spread 0.12-0.17 across repeats. Fix shipped as 2.44.8.
Hypothesis: on companion/L the user's own facts land on a relative's entity
  ("User's sister … has played the bass since school") after one sentence about
  that relative, and one intention is stored as five paraphrases. Fixing
  attribution alone lifts companion/L recall@old from 0.25-0.50 to >= 0.75
  (full history: 0.88).
Method: read engine/extractor prompt + cloud/store/_entities dedup; add a test
  from the exact corpus turns; fix; re-run mengram companion S/M/L x3.
Verify (pre-registered): companion/L mean recall@old over 3 repeats >= 0.75
  with junk_rate not worse than r1 (0.50). Else reject with numbers.
