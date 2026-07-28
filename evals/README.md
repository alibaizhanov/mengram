# Extraction Quality Evals

The boring moat. Golden cases in `extraction_cases.yaml` run through the **real**
`ConversationExtractor` (same construction path as `cloud/api.py`) and assert what
must / must not be extracted.

## Rules

1. **Every real user complaint becomes a case here, forever.** A bug fixed without
   an eval will regress silently.
2. No change to extraction prompts ships without this suite passing.
3. Grind loop (Casetext method): add cases → run → tweak prompt → repeat until 97%+.
   Do not stop at 60% and blame the model.

## Run

```bash
OPENAI_API_KEY=... LLM_PROVIDER=openai LLM_MODEL=gpt-5.4-mini \
    python3 evals/run_extraction_evals.py            # all cases
python3 evals/run_extraction_evals.py --case secrets-never-stored --verbose
```

Costs real tokens (~10 LLM calls per full run). Exit 0 = all pass.
`ADVISORY` lines report gaps that don't fail the run yet (e.g. category tagging).

## Check types

- `must_extract` / `must_not_extract` — keyword presence across all extracted content
- `expect_attribution` — per-entity: `{entity_keyword, must_have, must_not_have, must_have_one_of}`.
  This is how #54 (third-party facts polluting the user) is caught precisely.
- `qualified_past` — `{keyword, markers}`: every fact mentioning the keyword must carry a
  past-tense marker (supersession — a bare "uses X" would be the data-loss bug)
- `expect_procedure` / `expect_episode_keyword` / `expect_no_output`
- `known_gap: true` — reports failures but does not fail the suite (tracked TODO, not regression)

## Current cases (10, 9 enforced + 1 known-gap)

Sourced from real incidents: #54 identity pollution, Apr-2026 supersession data-loss,
capture-boundary churn, assistant-noise filtering, `steps list[dict]` regression,
secrets hygiene, multilingual + Russian supersession, transient-state filtering.

**Known gap:** the extractor does not yet emit category tags (health/legal/financial…),
so capture-policy can't drop them pre-persistence at extraction time. Tracked in
`health-content-flagged` (known_gap). Next grind target.
