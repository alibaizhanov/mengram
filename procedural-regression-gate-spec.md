# Spec — Cross-Procedure Regression Gate

**Status:** design · 2026-07-28
**Wedge:** the one open problem verified against the 2025–26 literature first-hand.
"Revising workflow A silently breaks workflow B that depended on it." Every
procedural-memory paper (MACLA 2512.18950, PRAXIS 2511.22074, GovMem 2607.02579,
EvoSkill 2603.02766, AFTER 2606.23127) leaves this explicitly open. AFTER:
"whether skills … can be optimized independently without cross-skill
interference." EvoSkill: "does not address regressions when newly added skills
interfere with existing ones."

**Positioning (HN-safe):** NOT "we invented procedural memory" (prior art). Say:
**"the first agent memory with regression tests — it catches when one learned
procedure silently breaks another."** Force multiplier = a public benchmark
(`ProcInterfere`) that shows existing systems regress and Mengram catches it.

---

## The bug this closes (in our own code)

`store.evolve_procedure()` (`cloud/store.py:5551`) marks the old version
`is_current = FALSE` and writes a new `is_current = TRUE` version — with **zero
check on any other procedure that shared surface with it.** If procedure B's
recall or steps depended on an assumption A guaranteed, A's revision can silently
invalidate B. Today nothing detects it. This is procedural-v2-spec item #8,
productized.

---

## Data we already have (no new tables required for v1)

- `procedures`: `id, name, steps (jsonb: [{step,action,detail}]), entity_names[],
  trigger_condition, success_count, fail_count, version, parent_version_id,
  is_current, metadata (has `preconditions`), sub_user_id`.
- `procedure_evolution`: version_before/after, diff, episode_id.
- `episodes`: linked_procedure_id, failed_at_step, outcome, happened_at — these
  are the **canary traces** (real past invocations with outcomes).

## New concept: "shared surface"

Two procedures share surface if they overlap on any of:
1. **entities** — `entity_names[]` intersection (already indexed, GIN
   `idx_procedures_entities`).
2. **tools/actions** — normalized verbs/tool tokens extracted from `steps[].action`
   (e.g. "run migrations", "deploy", "psql", an API name).
3. **preconditions** — overlapping keys in `metadata.preconditions`.

v1 uses (1) + (3) — both are structured and cheap. (2) is a follow-up (needs a
small action-tokenizer).

---

## Mechanism (v1 — deterministic, no new model calls on the hot path)

Hook a check **inside `evolve_procedure`, after building the new version but
BEFORE flipping `is_current`** (or: build new row as `is_current=FALSE,
status='pending'`, run the gate, then promote):

```
def evolve_procedure(...):
    old = get_procedure_by_id(...)
    new = build_new_version(...)           # not yet current
    dependents = find_shared_surface(user_id, old, exclude=old.id, sub_user_id)
    regressions = []
    for B in dependents:
        # B's preconditions that A's revision may have invalidated
        broke = preconditions_no_longer_guaranteed(A_new=new, B=B)
        if broke:
            regressions.append((B, broke))
    if regressions:
        mark new version status='needs_review', is_current=FALSE
        emit event 'procedure_revision_quarantined' (name, dependents, reasons)
        return {status: 'quarantined', new_version_id, regressions}
    else:
        promote new to is_current=TRUE   # existing behaviour
        return {status: 'promoted', new_version_id}
```

**`preconditions_no_longer_guaranteed(A_new, B)` — v1 deterministic rule:**
A revision commonly *adds a precondition* ("verify migrations before push"). If
B's steps or trigger relied on A completing WITHOUT that precondition (i.e. B
invokes A's effect and A now demands something B never supplies), flag it. v1
signal, cheap and honest:
- A_new added a precondition key K (diff of `metadata.preconditions` vs parent).
- B references A's name/entity in `entity_names` or `steps[].detail`, AND B has
  no step/precondition satisfying K.
→ candidate regression. Conservative: false positives go to review, never silently
break (ties → quarantine, matching our #62 fix philosophy).

**Optional v2 — canary replay (stronger, uses episodes):** for each dependent B,
take its last N successful `episodes` (linked_procedure_id=B, outcome success),
and LLM-judge whether B's expected effect still holds under A_new's changed
contract. Heavier (model calls), gate behind a flag / async. v1 ships without it.

---

## Surfaces

- **New table (v2 only, optional):** `procedure_dependencies(from_id, to_id,
  surface_type, created_at)` — cache the graph. v1 computes on the fly (few
  procedures per user; entity GIN index makes it cheap).
- **Status field:** reuse a `metadata.status` = `active | needs_review |
  quarantined` on the new version instead of a schema migration for v1.
- **Event:** `procedure_revision_quarantined` via the existing proactive-event
  path (same plumbing as `procedure_evolved`). Surfaces in the weekly report and
  (later) the Playbook review gate → this is where "human approves revisions"
  (procedural-v2 item #5) lands.
- **Recall:** quarantined versions are NOT served; the last known-good
  `is_current` stays authoritative until review. So a bad revision can't reach an
  agent — the whole point.

---

## The benchmark: `ProcInterfere` (the Show-HN artifact)

Small public repo, ~100–300 paired tasks:
- Procedure A and dependent B with a real shared surface.
- A revision to A that *should* be caught (breaks B) vs one that *shouldn't*
  (independent).
- Metric: **silent-regression rate** = % of B-breaking revisions a system
  promotes without flagging.
- Baselines: an AWM/Memp-style store, plain "append + latest wins."
- Result by construction: baselines regress silently; Mengram quarantines.
- This is the defensible piece — a *measurement nobody has*, not a mechanism a
  lab can out-scale. STALE (2605.06527) did exactly this for fact staleness
  (quantified the gap at 55.2%) and became the citation magnet.

---

## Build order (solo-founder sized)

1. `find_shared_surface()` + `preconditions_no_longer_guaranteed()` (pure
   functions, unit-tested against synthetic procedure pairs) — **like the #62
   fix: deterministic core first, tests first.**
2. Wire the gate into `evolve_procedure`, quarantine path + event.
3. Eval cases in `evals/` (paired procedures, like the extraction golden cases).
4. `ProcInterfere` benchmark repo (public) — the Show HN.
5. Playbook review-gate UI consumes `needs_review` (October).

## Honesty guardrails (must hold in any public claim)

- Never "first/only failure-driven procedural memory" — MACLA/PRAXIS = prior art.
- Never claim fact-staleness novelty — STALE/TOKI/Zep own it.
- DO claim: first to detect cross-procedure interference / regression-gate
  revisions; back it with the benchmark number, not adjectives.

Related: [[innovation-cross-procedure-2026-07-28]], [[procedural-v2-spec]].
