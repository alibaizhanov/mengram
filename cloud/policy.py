"""Turn a workflow's track record into a decision about running it.

Retrieval already ranks a weak procedure lower. That is not the same as
changing what the agent is allowed to do with it: a workflow that has never
been run, or that exists only because an earlier version failed, should not
be executed on the agent's own say-so. It should come back to the human as a
plan first.

This module is the pure part of that gate. It takes a procedure record (the
shape `/v1/procedures/search` returns, or a memfmt file) and a shell command,
and says whether the command should be confirmed before it runs, and why.
The Claude Code hook in `cli.py` is a thin wrapper around `decide()`. Nothing
here touches the network, and no decision is ever `deny`: the memory can ask,
it does not get to forbid.
"""

from __future__ import annotations

import re

from cloud.reliability import estimate

#: Commands that look like a workflow rather than a lookup. Anything else is
#: waved through without a search, which keeps the hook cheap: a `ls` should
#: never cost a memory query. Override with MENGRAM_POLICY_PATTERN.
DEFAULT_PATTERN = (
    r"\b(deploy|publish|release|migrat\w*|rollback|terraform|pulumi|ansible|"
    r"kubectl|helm|docker\s+(compose\s+)?(up|push|build|run)|"
    r"git\s+push|twine\s+upload|npm\s+publish|cargo\s+publish|"
    r"railway|vercel|heroku|fly\s+deploy|gcloud|aws\s+\w+|"
    r"ssh|scp|rsync|psql|mysql|drop\s+table|truncate|rm\s+-rf?)\b"
)

#: Below this a procedure with a real record still gets a confirmation.
DEFAULT_MIN_RELIABLE = 70

_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "then", "that", "this",
    "when", "after", "before", "your", "run", "runs", "running", "make",
    "using", "used", "step", "steps", "check", "verify", "wait", "true",
    "false", "none", "null", "echo", "cd", "&&", "||",
}

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_./-]{2,}")


def looks_like_workflow(command: str, pattern: str | None = None) -> bool:
    """Is this command worth a memory lookup at all?"""
    if not command:
        return False
    pat = DEFAULT_PATTERN if pattern is None else pattern
    if pat.strip() in ("", ".*", "*"):
        return True
    try:
        return re.search(pat, command, re.IGNORECASE) is not None
    except re.error:
        return re.search(DEFAULT_PATTERN, command, re.IGNORECASE) is not None


def tokens(text: str) -> set[str]:
    """Words worth matching on: lowercase, 3+ chars, not glue."""
    return {
        w.lower().strip("./-")
        for w in _WORD.findall(text or "")
        if w.lower() not in _STOPWORDS
    } - {""}


def _procedure_text(proc: dict) -> str:
    parts = [proc.get("name") or "", proc.get("trigger_condition") or proc.get("trigger") or ""]
    for step in proc.get("steps") or []:
        if isinstance(step, dict):
            parts.append(step.get("action") or "")
            parts.append(step.get("detail") or "")
        else:
            parts.append(str(step))
    return " ".join(parts)


def procedure_matches(proc: dict, command: str) -> bool:
    """Lexical sanity check on top of semantic search.

    Vector search returns *something* for almost any query. Before a
    confirmation prompt interrupts the user, the procedure has to share at
    least one real word with the command it is supposedly about.
    """
    return bool(tokens(_procedure_text(proc)) & tokens(command))


def reliability_of(proc: dict) -> str:
    """The record in words, computed here when the source did not include it."""
    label = proc.get("reliability")
    if isinstance(label, str) and label:
        return label
    return estimate(int(proc.get("success_count") or 0), int(proc.get("fail_count") or 0))


def parse_reliability(label: str) -> tuple[str, int | None]:
    """`untested` → ("untested", None); `61% expected` → ("expected", 61)."""
    label = (label or "").strip().lower()
    if label == "untested" or not label:
        return "untested", None
    m = re.match(r"(\d+)%\s+(expected|reliable)", label)
    if not m:
        return "untested", None
    return m.group(2), int(m.group(1))


def decide(proc: dict, command: str, min_reliable: int = DEFAULT_MIN_RELIABLE) -> dict | None:
    """Should this command be confirmed before it runs?

    Returns None when the record is strong enough to let the agent act on it
    alone. Otherwise returns `{"decision": "ask", "reason": ..., "plan": ...}`
    where `reason` is for the human and `plan` is for the agent.
    """
    if not proc or not procedure_matches(proc, command):
        return None

    name = proc.get("name") or "unnamed workflow"
    version = proc.get("version") or 1
    label = reliability_of(proc)
    kind, pct = parse_reliability(label)
    s = int(proc.get("success_count") or 0)
    f = int(proc.get("fail_count") or 0)

    if kind == "untested":
        why = f"'{name}' has never been run"
    elif kind == "expected":
        why = (f"'{name}' v{version} has no runs of its own — {pct}% is inherited "
               f"from the version it replaced")
    elif pct is not None and pct < min_reliable:
        why = f"'{name}' is {pct}% reliable ({s}✓/{f}✗), below the {min_reliable}% bar"
    else:
        return None

    return {
        "decision": "ask",
        "reason": f"Mengram: learned workflow {why}. Review the plan before it runs.",
        "plan": _plan(proc, label),
        "name": name,
        "reliability": label,
    }


def _plan(proc: dict, label: str) -> str:
    lines = [f"[Mengram] Matched learned workflow '{proc.get('name')}' (v{proc.get('version') or 1}, {label})."]
    trig = proc.get("trigger_condition") or proc.get("trigger")
    if trig:
        lines.append(f"When: {trig}")
    steps = proc.get("steps") or []
    if steps:
        lines.append("Steps on record:")
        for i, step in enumerate(steps, 1):
            if isinstance(step, dict):
                text = step.get("action") or ""
                if step.get("detail"):
                    text += f" — {step['detail']}"
                rel = step.get("reliability")
                if rel:
                    text += f" ({rel})"
            else:
                text = str(step)
            lines.append(f"  {i}. {text}")
    pre = (proc.get("metadata") or {}).get("preconditions") or proc.get("preconditions")
    if pre:
        lines.append("Preconditions: " + "; ".join(str(p) for p in (pre if isinstance(pre, list) else [pre])))
    if proc.get("last_failure"):
        when = f"{proc['last_failed']}: " if proc.get("last_failed") else ""
        lines.append(f"Last failure: {when}{proc['last_failure']}")
    lines.append("The evidence for this workflow is weak, so the user was asked to confirm. "
                 "If they decline, show them the plan and what you would verify first.")
    return "\n".join(lines)


def hook_output(verdict: dict | None, verbose_marker: str | None = None) -> dict | None:
    """The JSON a Claude Code PreToolUse hook prints for this verdict.

    None means print nothing: no decision, the call proceeds as it would have.
    """
    if verdict is None:
        if verbose_marker:
            return {"systemMessage": verbose_marker}
        return None
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict["decision"],
            "permissionDecisionReason": verdict["reason"],
            "additionalContext": verdict["plan"],
        }
    }
    if verbose_marker:
        out["systemMessage"] = verbose_marker
    return out


def best_match(procs: list[dict], command: str) -> dict | None:
    """First procedure that actually shares words with the command."""
    for proc in procs or []:
        if procedure_matches(proc, command):
            return proc
    return None


def memfmt_procedures(root: str) -> list[dict]:
    """Procedures from a memfmt folder, in the dict shape `decide()` reads.

    memfmt is optional: if it is not installed, local mode is simply off.
    """
    try:
        import memfmt  # type: ignore
        memory = memfmt.load(root)
    except Exception:
        # Not installed, a namespace package shadowing it, or a folder that
        # is not a memory: local mode is off, and the hook stays silent.
        return []
    out = []
    for p in memory.procedures:
        out.append({
            "name": p.name,
            "version": p.version,
            "success_count": p.success_count,
            "fail_count": p.fail_count,
            "trigger_condition": p.trigger,
            "preconditions": list(p.preconditions or []),
            # memfmt 0.5 fields; absent on an older library, and then absent here.
            "last_failure": getattr(p, "last_failure", None),
            "last_failed": getattr(p, "last_failed", None),
            "steps": [
                {"action": s.action, "detail": s.detail,
                 "success_count": s.success_count, "fail_count": s.fail_count}
                for s in (p.steps or [])
            ],
        })
    return out
