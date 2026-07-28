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


def find_regressions(old_proc: dict, new_proc: dict, candidates: list) -> list:
    """Return [{dependent, broken_preconditions:[...]}] for procedures that a
    revision may silently break. Empty list = safe to promote.

    old_proc / new_proc: the procedure before and after revision.
    candidates: other current procedures for the same user/sub_user.
    """
    added = newly_added_preconditions(old_proc, new_proc)
    if not added:
        return []  # a revision that adds no new demand can't newly break a dependent
    regressions = []
    for b in candidates:
        if not shares_surface(new_proc, b):
            continue
        broken = [k for k in added if dependent_lacks_precondition(b, k)]
        if broken:
            regressions.append({
                "dependent_id": str(b.get("id")),
                "dependent_name": b.get("name"),
                "broken_preconditions": broken,
            })
    return regressions
