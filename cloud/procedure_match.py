"""What makes two procedures the same workflow, and how a run is credited.

Pure functions shared by the cloud store and the local folder store, kept
out of `store.py` so that the local mode never has to import psycopg2 to
decide whether "Deploying to Railway" is "Deploy to Railway".
"""

from __future__ import annotations

import re

_PROC_NAME_STOPWORDS = {
    "the", "a", "an", "to", "for", "of", "in", "on", "at", "by", "with", "and",
    "or", "via", "using", "use", "how", "into", "from", "up", "your", "my",
    "our", "new", "process", "procedure", "workflow", "steps", "step", "routine",
}


def _proc_stem(word: str) -> str:
    """Crude suffix stripping so 'deploying', 'deployed' and 'deploys' agree.

    Not a stemmer. It only has to make the same workflow, named three ways by
    three extraction runs, land on the same tokens; it does not have to be
    right about English."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3 and not word.endswith("ss"):
            return word[: -len(suffix)]
    return word


def _proc_name_tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {_proc_stem(w) for w in words if w not in _PROC_NAME_STOPWORDS and len(w) > 1}


def normalize_step(s) -> str:
    """A step as text. Steps are dicts like {"action": ..., "detail": ...};
    older rows and hand-written files may hold plain strings."""
    if isinstance(s, str):
        return s
    if isinstance(s, dict):
        return s.get("action", "") or s.get("step", "") or s.get("description", "") or str(s)
    return str(s)


def _proc_step_tokens(steps: list) -> set:
    out = set()
    for s in steps or []:
        out |= _proc_name_tokens(normalize_step(s))
    return out


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def procedure_similarity(name_a: str, steps_a: list, name_b: str, steps_b: list) -> tuple:
    """(name overlap, step overlap), each 0..1."""
    return (_jaccard(_proc_name_tokens(name_a), _proc_name_tokens(name_b)),
            _jaccard(_proc_step_tokens(steps_a), _proc_step_tokens(steps_b)))


def is_near_duplicate_procedure(name_sim: float, step_sim: float) -> bool:
    """Same workflow under a slightly different name.

    A near-identical name is enough on its own ("Deploy to Railway" vs
    "Deploying to Railway"). A merely similar name needs the steps to agree
    too, so "Deploy backend" and "Deploy docs" stay apart."""
    return name_sim >= 0.8 or (name_sim >= 0.5 and step_sim >= 0.5)


def apply_step_outcome(steps: list, success: bool, failed_at_step: int = None) -> list:
    """Credit the steps that ran and debit the one that broke.

    A run does not tell you one thing, it tells you as many things as there
    are steps. When step 3 of 5 fails, steps 1 and 2 *ran and worked* —
    recording only "the procedure failed" throws that away and makes every
    step look equally suspect. Steps after the failure never executed, so
    they learn nothing either way.
    """
    updated = []
    for i, step in enumerate(steps or [], 1):
        if not isinstance(step, dict):
            updated.append(step)
            continue
        step = dict(step)
        if success:
            ran, worked = True, True
        elif failed_at_step is None:
            ran, worked = False, False      # nothing to attribute
        else:
            ran, worked = i <= failed_at_step, i < failed_at_step
        if ran:
            key = "success_count" if worked else "fail_count"
            step[key] = int(step.get(key) or 0) + 1
        updated.append(step)
    return updated
