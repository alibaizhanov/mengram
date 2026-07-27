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

## Current cases (10)

Sourced from real incidents: #54 identity pollution, the Apr-2026 supersession
data-loss bug, the capture-boundary churn case, assistant-noise filtering (v2
prompt), `steps list[dict]` regression, secrets hygiene, multilingual promise.
