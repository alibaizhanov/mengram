"""Failure → revision, on files.

A workflow that failed is not just a workflow with a worse record. Somewhere a
belief turned out false, and the next version has to carry both the fix and
the belief, or the same failure comes back with a different step number. This
is the cloud's failure loop (`cloud/evolution.py`) applied to a memfmt folder:
same prompt, same regression gate, same rule that a version nobody has ever
run is revised in place rather than minted again.

What is different from the cloud is only where the result goes: a promoted
revision becomes the procedure file's next version, with the retiring record
on the `Evolution` line and the violated assumption in `last_failure`; a
revision the gate refuses goes to `.mengram/quarantine.json` for a human,
never to the agent.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path

from memfmt import Revision

from cloud.evolution import EVOLVE_ON_FAILURE_PROMPT
from cloud.regression_gate import find_regressions
from cloud.reliability import carry_step_history

from .store import LocalStore, _steps_as_dicts, _to_steps

logger = logging.getLogger("mengram.local")

QUARANTINE = Path(".mengram") / "quarantine.json"


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.split("\n") if not line.strip().startswith("```"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}") + 1
        if 0 <= start < end:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return None


def _gate_shape(p, steps: list | None = None, trigger: str | None = None,
                preconditions: list | None = None) -> dict:
    """A procedure as the regression gate expects to see it."""
    return {
        "id": p.id or p.name,
        "name": p.name,
        "entity_names": list(p.extra.get("entities") or []),
        "steps": _steps_as_dicts(steps if steps is not None else p.steps),
        "trigger_condition": trigger if trigger is not None else p.trigger,
        "metadata": {"preconditions": list(preconditions if preconditions is not None else p.preconditions)},
    }


def quarantine_path(store: LocalStore) -> Path:
    return store.root / QUARANTINE


def read_quarantine(store: LocalStore) -> list:
    path = quarantine_path(store)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def evolve_on_failure(store: LocalStore, name: str, context: str, llm_client,
                      failed_at_step: int | None = None) -> dict:
    """Ask the model what belief broke, then promote or quarantine the fix.

    Returns {"status": "promoted" | "revised_in_place" | "quarantined" |
    "no_change" | "error", ...}. Writes the tree (or the quarantine file).
    """
    p = store._procedure(name)
    if p is None:
        return {"status": "error", "error": "procedure not found", "name": name}

    steps_text = "\n".join(
        f"{i}. {s.action}" + (f" — {s.detail}" if s.detail else "")
        for i, s in enumerate(p.steps, 1)) or "(no steps)"
    prompt = EVOLVE_ON_FAILURE_PROMPT.format(
        procedure_name=p.name, trigger_condition=p.trigger or "N/A", steps_text=steps_text,
        episode_summary=f"Procedure '{p.name}' failed",
        episode_context=context or "N/A", episode_outcome="failure",
        failed_at_step=failed_at_step or "unknown",
    )
    try:
        result = _parse_json(llm_client.complete(prompt))
    except Exception as e:  # the model is the user's; its failures are not ours to hide
        return {"status": "error", "error": str(e), "name": p.name}
    if not result or not result.get("new_steps"):
        return {"status": "no_change", "name": p.name, "reason": "model returned no steps"}

    new_steps = [s for s in result["new_steps"] if isinstance(s, dict)]
    new_trigger = result.get("new_trigger") or None
    if isinstance(new_trigger, str) and new_trigger.strip().lower() in ("", "null", "none"):
        new_trigger = None
    assumption = " ".join(str(result.get("violated_assumption") or "").split())
    precondition = " ".join(str(result.get("precondition_check") or "").split())
    reason = " ".join(str(result.get("change_description") or result.get("change_type") or "revised").split())

    preconditions = list(p.preconditions)
    if precondition and precondition not in preconditions:
        preconditions = (preconditions + [precondition])[-10:]

    # --- the gate: does this fix silently break a neighbour? ---------------
    old_shape = _gate_shape(p)
    new_shape = _gate_shape(p, steps=new_steps, trigger=new_trigger or p.trigger, preconditions=preconditions)
    candidates = [_gate_shape(q) for q in store.memory.procedures if q is not p]
    regressions = find_regressions(old_shape, new_shape, candidates)
    if regressions:
        entry = {
            "procedure": p.name, "version": p.version, "date": _dt.date.today().isoformat(),
            "reason": reason, "violated_assumption": assumption or None,
            "precondition_check": precondition or None,
            "proposed_steps": [{"action": s.get("action"), "detail": s.get("detail")} for s in new_steps],
            "proposed_trigger": new_trigger, "regressions": regressions,
        }
        path = quarantine_path(store)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = read_quarantine(store)
        existing.append(entry)
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("quarantined revision of %r: breaks %s", p.name,
                    ", ".join(r["dependent_name"] for r in regressions))
        return {"status": "quarantined", "name": p.name, "regressions": regressions,
                "quarantine": str(path)}

    # --- promote --------------------------------------------------------------
    old_dicts = _steps_as_dicts(p.steps, with_counts=True)
    carried = carry_step_history(old_dicts, new_steps)
    today = _dt.date.today().isoformat()

    if p.success_count == 0:
        # Nothing was ever learned about this version: the fix is a better
        # description, not a new lineage. Same rule as the cloud (PR #89).
        p.steps = _to_steps(carried, keep_counts=True)
        status = "revised_in_place"
    else:
        p.evolution.append(Revision(
            version_before=p.version, version_after=p.version + 1, reason=reason, date=today,
            success_count=p.success_count, fail_count=p.fail_count))
        p.version += 1
        p.success_count, p.fail_count = 0, 0
        p.steps = _to_steps(carried, keep_counts=True)
        status = "promoted"

    if new_trigger:
        p.trigger = new_trigger
    p.preconditions = preconditions
    if assumption:
        p.last_failure, p.last_failed = assumption, today
    store.save()
    return {"status": status, "name": p.name, "version": p.version,
            "violated_assumption": assumption or None, "precondition_check": precondition or None,
            "reliability": p.reliability}
