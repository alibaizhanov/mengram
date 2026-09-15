# Corpus (gitignored except this file)

Files: {companion,support,coding}_{S,M,L}.jsonl — one dialogue turn per line,
{"t": <simulated day>, "role": "user"|"assistant", "text": ...}.
S = 7 simulated days, M = 30, L = 90.

Sources, in order of preference:
1. Ali's own Claude Code sessions (~/.claude/projects), already consented,
   coding type. Strip hook context and system-reminder wrappers (see the
   2026-09-15 feedback-loop check: the CLI path is clean, the raw transcript is not).
2. Synthetic dialogues generated with a fixed seed (bench_memory.py generate),
   companion and support types: a fixed cast of people/customers with facts
   planted on known days, so recall@old has ground truth.
3. Customer dialogues only with explicit consent, never by default.

Ground truth lives next to each file: <name>.truth.json — the planted facts,
the day they were planted, the questions that need them, and the distractors.
