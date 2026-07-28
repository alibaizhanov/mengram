# ProcInterfere — does your agent's memory have regression tests?

The first benchmark for **cross-procedure interference**: when an agent's memory
revises one learned workflow, does it notice that the revision silently breaks
another workflow that depended on it?

Every procedural-memory system from 2025–2026 evaluates learned skills **in
isolation** — MACLA ([arXiv:2512.18950](https://arxiv.org/abs/2512.18950)),
PRAXIS ([2511.22074](https://arxiv.org/abs/2511.22074)), Memp
([2508.06433](https://arxiv.org/abs/2508.06433)), EvoSkill
([2603.02766](https://arxiv.org/abs/2603.02766)), AFTER
([2606.23127](https://arxiv.org/abs/2606.23127)). The AFTER authors list it as an
open problem: *"whether skills can be optimized independently without cross-skill
interference."* Nobody measures it. This does.

## The metric

**Silent-regression rate** — the % of memory revisions that break a dependent
procedure and get promoted anyway, with no flag. Lower is better.

Over 18 paired cases across 12 domains (Postgres, S3, Stripe, GitHub, Terraform,
Redis, Kafka, SQS, Cloudflare, OpenAI, LaunchDarkly, DNS):

```
system          silent-regression   false-quarantine
----------------------------------------------------
latest-wins                 100%                0%
append-only                 100%                0%
mengram-gate                  0%                0%
```

- `latest-wins` — the industry default: the newest version of a procedure always
  wins. No interference check.
- `append-only` — keep every version, still serve the newest, unchecked.
- `mengram-gate` — Mengram's [cross-procedure regression gate](https://github.com/alibaizhanov/mengram/blob/main/cloud/regression_gate.py):
  before promoting a revision, it checks whether the revision adds a precondition
  a dependent procedure doesn't satisfy, and quarantines it for review instead of
  shipping it to the agent.

## Run it

```bash
git clone https://github.com/alibaizhanov/mengram
cd mengram/benchmark/procinterfere
python run.py
```

No account, no key — the gate is pure deterministic code. Exit 0 if the gate
beats the baselines.

## What a case looks like

A procedure `A` is revised to add a precondition (e.g. "run migrations before
push"). A second procedure `B` shares surface with `A` (same database) but never
runs migrations. The revision to `A` silently invalidates `B`. A good system
flags it; `latest-wins` ships it.

Cases live in [`cases.jsonl`](cases.jsonl) — `should_flag` marks the breaking
ones. Contributions of new interference patterns welcome.

## Honest scope

This is a *detection* benchmark, not a claim that Mengram invented procedural
memory (it didn't — see the papers above). The claim is narrower and checkable:
**Mengram is the first agent memory that catches cross-procedure interference
before a revision reaches the agent.** Built by the team at
[mengram.io](https://mengram.io) (open-core, free tier).
