"""Cross-procedure regression gate (v1, deterministic).

The one open problem the 2025-26 procedural-memory literature leaves unclaimed:
revising workflow A can silently break workflow B that depended on it. Every
paper (MACLA 2512.18950, PRAXIS 2511.22074, GovMem 2607.02579, EvoSkill, AFTER)
evaluates procedures in isolation. This module detects the interference before a
revision is promoted, and quarantines it for review instead of shipping it to an
agent.

Pure functions — no DB, no model calls on the hot path. The store wires these in
around evolve_procedure(). Philosophy matches the #62 fix: ties go to safety
(quarantine), never silently promote a possibly-breaking revision.

Procedure shape (as returned by store.get_procedures / get_procedure_by_id):
    {
      "id", "name", "entity_names": [...],
      "steps": [{"step", "action", "detail"}, ...],
      "trigger_condition": str | None,
      "metadata": {"preconditions": [str, ...], ...},
    }
"""
from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9_]+")
_STOP = frozenset({
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are",
    "be", "it", "this", "that", "with", "before", "after", "run", "check",
    "verify", "ensure", "make", "sure", "must", "should", "first", "then",
})


def _tokens(text: str) -> set:
    if not text:
        return set()
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def _procedure_text(proc: dict) -> str:
    """All searchable text of a procedure: steps + trigger + entities."""
    parts = [proc.get("trigger_condition") or ""]
    for s in proc.get("steps") or []:
        if isinstance(s, dict):
            parts.append(f"{s.get('action', '')} {s.get('detail', '')}")
    parts.extend(proc.get("entity_names") or [])
    return " ".join(parts)


def preconditions(proc: dict) -> list:
    md = proc.get("metadata") or {}
    return [p for p in (md.get("preconditions") or []) if isinstance(p, str) and p.strip()]


def shares_surface(a: dict, b: dict) -> bool:
    """True if two procedures overlap on entities or precondition vocabulary.

    v1 surfaces: entity_names intersection, OR b references a's name/entities in
    its own text (b depends on a), OR shared precondition tokens.
    """
    if str(a.get("id")) == str(b.get("id")):
        return False
    a_ents = {e.lower() for e in (a.get("entity_names") or [])}
    b_ents = {e.lower() for e in (b.get("entity_names") or [])}
    if a_ents & b_ents:
        return True
    # b references a by name (dependency edge)
    b_text = _procedure_text(b).lower()
    if a.get("name") and a["name"].lower() in b_text:
        return True
    # shared entity mentioned in b's text
    if any(e in b_text for e in a_ents):
        return True
    return False


def newly_added_preconditions(old_proc: dict, new_proc: dict) -> list:
    """Preconditions present in the revised version but not the old one."""
    before = set(preconditions(old_proc))
    return [p for p in preconditions(new_proc) if p not in before]


_NEGATORS = frozenset({"no", "not", "without", "skip", "skips", "skipping",
                       "never", "dont", "doesnt", "don't", "doesn't", "n't"})


def _covered_tokens(text: str) -> set:
    """Tokens that positively appear in text, EXCLUDING tokens inside a negated
    span. "no encryption header" must not count as covering encryption/header —
    the dependent explicitly does NOT do it (fixes the s3-encryption miss)."""
    words = _WORD.findall(text.lower())
    covered, i = set(), 0
    while i < len(words):
        w = words[i]
        if w in _NEGATORS:
            i += 4  # drop the next few tokens governed by the negation
            continue
        if w not in _STOP and len(w) > 2:
            covered.add(w)
        i += 1
    return covered


def dependent_lacks_precondition(dependent: dict, precondition: str) -> bool:
    """True if `dependent` does NOT already satisfy `precondition`.

    A revision to A that adds a precondition K can break B if B invokes A's
    effect but never satisfies K itself. We approximate 'satisfies K' by
    negation-aware token overlap between K and B's own steps/preconditions: if B
    positively does K's key tokens, assume it handles it; otherwise candidate break.
    """
    k_tokens = _tokens(precondition)
    if not k_tokens:
        return False
    covered = _covered_tokens(_procedure_text(dependent))
    for p in preconditions(dependent):
        covered |= _covered_tokens(p)
    overlap = len(k_tokens & covered)
    return overlap < max(1, (len(k_tokens) + 1) // 2)


def _step_text(step) -> str:
    """One step as searchable text, whether it is a dict or a bare string."""
    if isinstance(step, dict):
        return f"{step.get('action', '')} {step.get('detail', '')}".strip()
    return str(step or "")


def _same_action(a_tokens: set, b_tokens: set) -> bool:
    """Whether two step texts describe the same action.

    Half the shorter side's tokens, so "push code" matches "push code to
    Railway" without matching everything that merely mentions Railway.
    """
    if not a_tokens or not b_tokens:
        return False
    overlap = len(a_tokens & b_tokens)
    return overlap >= max(1, (min(len(a_tokens), len(b_tokens)) + 1) // 2)


def newly_ordered_steps(old_proc: dict, new_proc: dict) -> list:
    """(prerequisite, action) pairs the revision newly established.

    A step inserted ahead of one that already existed is an ordering constraint
    whether or not anybody wrote it down as a precondition: putting "run
    migrations" before "push code" says pushing first is now wrong. Without
    this the gate sees nothing at all in a revision that only moves steps
    around, which is exactly how the constraint usually arrives.

    Only newly inserted steps count. Reshuffling two steps that both already
    existed is a weaker signal and would cost false quarantines, so v1 leaves
    it alone rather than guess.
    """
    old_keys = [_tokens(_step_text(s)) for s in (old_proc.get("steps") or [])]
    new_texts = [_step_text(s) for s in (new_proc.get("steps") or [])]
    pairs = []
    for i, text in enumerate(new_texts):
        tokens = _tokens(text)
        if not tokens or any(_same_action(tokens, k) for k in old_keys):
            continue                      # existed before, so not newly introduced
        for later in new_texts[i + 1:]:
            later_tokens = _tokens(later)
            if any(_same_action(later_tokens, k) for k in old_keys):
                pairs.append((text, later))
    return pairs


def dependent_violates_order(dependent: dict, prerequisite: str, action: str) -> bool:
    """True if `dependent` performs `action` without `prerequisite` before it.

    Covers both ways this goes wrong with one check: the prerequisite missing
    entirely, and the prerequisite present but in a later step. The matched
    step itself counts as satisfying it — a dependent whose single step reads
    "register the schema then re-produce" does do both, and deciding it got
    the order wrong inside one sentence is a guess that costs a false
    quarantine. Ordering *within* a step is out of scope; ordering *between*
    steps is what this sees.
    """
    action_tokens = _tokens(action)
    prerequisite_tokens = _tokens(prerequisite)
    if not action_tokens or not prerequisite_tokens:
        return False

    steps = [_step_text(s) for s in (dependent.get("steps") or [])]
    position = next((i for i, text in enumerate(steps)
                     if _same_action(action_tokens, _tokens(text))), None)
    if position is None:
        return False                      # it never does the thing, so nothing to break

    earlier = _covered_tokens(" ".join(steps[:position + 1]))
    for p in preconditions(dependent):
        earlier |= _covered_tokens(p)
    overlap = len(prerequisite_tokens & earlier)
    return overlap < max(1, (len(prerequisite_tokens) + 1) // 2)


def find_regressions(old_proc: dict, new_proc: dict, candidates: list) -> list:
    """Return [{dependent, broken_preconditions:[...]}] for procedures that a
    revision may silently break. Empty list = safe to promote.

    old_proc / new_proc: the procedure before and after revision.
    candidates: other current procedures for the same user/sub_user.
    """
    added = newly_added_preconditions(old_proc, new_proc)
    orderings = newly_ordered_steps(old_proc, new_proc)
    if not added and not orderings:
        return []  # a revision that adds no new demand can't newly break a dependent
    regressions = []
    for b in candidates:
        if not shares_surface(new_proc, b):
            continue
        broken = [k for k in added if dependent_lacks_precondition(b, k)]
        broken_order = [[prerequisite, action] for prerequisite, action in orderings
                        if dependent_violates_order(b, prerequisite, action)]
        if broken or broken_order:
            regressions.append({
                "dependent_id": str(b.get("id")),
                "dependent_name": b.get("name"),
                "broken_preconditions": broken,
                "broken_orderings": broken_order,
            })
    return regressions
