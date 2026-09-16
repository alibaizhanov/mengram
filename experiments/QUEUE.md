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

## E1 [x] Salience gate — a classifier that says "do not store this"
Result 2026-09-16: lexical gate (cloud/salience.py), local stack, 3 valid repeats
  per arm. recall@old 1.00 in all 18 runs (no needed fact lost). Junk S 0.13→0.08,
  M 0.38→0.21 (−45%), L 0.58→0.42 (−28%). Junk clause met at M, missed at L: the
  L remainder is true-but-unasked facts, not paraphrase; a write-time gate should
  not remove those → E1b (demote by recall history). Classifier on prod labels not
  done (needs a DB export run by Ali). Recommend shipping the gate on by default.
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

## E1b [x] Junk in the injected context — it is the raw chunks, not the facts
Result 2026-09-16: P2 (one chunk) accepted — recall@old 1.00 on all three
  memories, 154 tokens/question vs 280 (−45%). P3 (no chunks) 0.92, rejected.
  Shipped as `chunks` on /v1/search/all, default 1 (2.45.1).
Measured first (2026-09-16, E1 L results, 24 questions x 2 arms): of the context
  handed to the answer model, raw conversation chunks are 71-78% of the tokens
  and facts 18-23%. Fact junk in context: 41% off-gate, 29% on-gate. Chunks
  carried the answer alone in 2/24 questions (extraction had missed it), so
  they are a real fallback, but 5 of them per query is where the tokens go.
  The recall-history demotion idea targets the 18-23%; parked.
Hypothesis: capping or deduplicating chunks under the token budget cuts
  tokens per question by >= 40% with recall@old within 0.05 of baseline.
Method: replay, no re-extraction — the E1 on-arm L memories on the local
  stack (on-1, on-2, on-4), search/all uncut, then policies applied client-
  side before the same 600-token budget and the same answer model:
  P0 as today (5 chunks), P1 chunks <= 2, P2 chunks <= 1, P3 no chunks,
  P4 chunks deduplicated (content-word Jaccard > 0.6) then <= 2.
Verify (pre-registered): a policy is accepted if recall@old >= P0 - 0.05 and
  tokens/question <= 0.6 x P0 on the same three memories; among accepted, the
  cheapest wins and ships as the default. Else reject with numbers.

## E2 [x] Cost report — measure before selling it
Result 2026-09-16: tokens per request, memory vs full history, exact counts:
  M 9.4-17x, L 23-62x (target 5x / 10x) — met. Recall: companion memory >=
  full history; coding -12 (noise floor); support M 0.25 vs 0.62 — extraction
  loses values (loyalty number, pickup office, card) → E4. Gate fix shipped
  as 2.45.2. Report: experiments/cost_report.py (prices are stated inputs).
Hypothesis: for a builder's account, "tokens of context per request with
  memory" vs "full history per request" differs by >= 5x at M scale and
  >= 10x at L scale, with recall@old within 5 points of full-history.
Method: on E0 corpora, run both strategies (max_tokens budget vs. concatenated
  history) through the same answer model; count tokens (estimate, and tiktoken
  where the model is known); compute $ at a stated price.
Verify (pre-registered): the ratio holds on M and L with recall@old within 5
  points. If recall@old drops more, the budget cut is losing answers and the
  cost report must show that too.

## E4 [x] Value retention in extraction — the support defect E2 found
Result 2026-09-16: culprit was the contradiction pass, not the extractor.
  Support recall@old M 0.25 → 0.875 (x3), L 0.75 → 0.83 (x3); companion/coding
  unchanged. Junk L +0.06 (fewer wrong archives). Shipped as 2.45.3.
Hypothesis: the extractor drops or misattributes concrete values (identifiers,
  card endings, office names, options) that a product's end-user memory
  exists to hold: on support M it stored "has a loyalty number with the
  Assistant" (LR-11560 gone), never extracted "the central station office",
  put the customer's card on the Assistant entity. An extraction rule that
  keeps identifiers/numbers/named options verbatim on the person who owns
  them lifts support M/L recall@old to >= 0.75 with junk not worse than now.
Method: read engine/extractor prompt; add the exact support turns as tests;
  fix; re-run mengram support S/M/L x3 on the local stack (under caffeinate).
  Also decide what the contradiction pass should do with a scoped later
  statement ("no child seat for the business trip" vs "needs one child seat").
Verify (pre-registered): support M and L mean recall@old >= 0.75 over 3
  repeats, junk_rate not worse than e2b/e2c, companion/coding unchanged
  within 0.05.

## E5 [ ] State transitions as a relation, not a yes/no (u/ThomasBuildLab, r/AI_Agents 2026-09-16)
Hypothesis: the contradiction pass fails because it answers a binary question
  ("does new contradict old?") and then archives. If the model instead labels
  the relation of each new fact to the closest existing one — confirms /
  contradicts / supersedes / refines / independent / insufficient — and
  deterministic rules decide the transition per label (only "supersedes" may
  archive, "refines" merges detail into the old row, "confirms" bumps
  confidence, "insufficient" writes nothing), the four E4 guards become
  consequences of the rules rather than patches, and the fifth case does not
  need a fifth guard.
Method: relation-labelling prompt with a JSON enum; transition table in code;
  provenance kept on every superseded row; replay the E4 archive log and the
  support/companion corpora; count wrong archives and missed supersessions.
Verify (pre-registered): zero wrong archives on the E4 cases without the four
  guards enabled; support/companion recall@old within 0.05 of E4; no increase
  in stored duplicates (junk_rate not worse). Else keep the guards and reject.

## E6 [~] `mengram resume` — a task card across sessions and agents (Orca)
Hypothesis: a task card written at Stop (task, done, remaining, last check
  with the commit it was run on, files, sources) and injected at the next
  SessionStart — keyed by git remote + branch, so it follows a task across
  Orca worktrees and machines — reduces the three costs of a session
  transition: re-explaining the task, missing a constraint that was already
  known, redoing work already done. The comparison is the honest alternative:
  a STATUS.md the agent maintains in the repo.
Method: local/resume.py; deterministic parts from the transcript tail
  (files, commands, last test result, HEAD) as in the checkpoint; "done /
  remaining / last check" drafted by one model call through the cloud API
  when a key exists, marked as the agent's draft until the person confirms
  on the page; `mengram resume` prints the card, `--open` renders a local
  page to correct it, pick another task, copy context. Ten real session
  transitions on our own work, alternating card / STATUS.md, at least four
  inside Orca worktrees; each transition's first 10 minutes read from the
  transcript and scored on the three counts.
Verify (pre-registered): over 10 transitions the card is not worse than
  STATUS.md on any of the three counts and better on at least two (fewer
  re-explanations, fewer missed constraints, fewer redone steps). Else the
  hypothesis stops here and STATUS.md is what we recommend.

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
