"""What the agent was about to do, written down before it does it.

The post-tool event arrives only for commands that succeeded. Everything else
— a command that exited non-zero, one the user declined, one the host refused
to run — leaves nothing but a line in the transcript. Reading failures back
out of that transcript meant matching command text to workflow steps a second
time, after the fact, with a cursor that could skip past the evidence.

So the gate writes its judgement down at PreToolUse, keyed by the id the host
gives the tool call: *this* command is step N of *this* workflow. The outcome
half closes exactly that record, by id, never by guessing again. An intent
still open when the next event comes round is resolved against the transcript
by the same id — and only a result that begins `Exit code N` counts against
the step. A declined or refused command never ran, and says nothing.

Kept in `.mengram/intents.json`, small and bounded: intents are opened only
for commands that matched a step, which is a few in a hundred.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

FILE = "intents.json"

#: More open intents than this means the outcome hook has not been running;
#: the oldest are dropped rather than kept forever.
MAX_OPEN = 200

#: An intent nobody has resolved in a week refers to a session long gone.
MAX_AGE_SECONDS = 7 * 24 * 3600


def path(root) -> Path:
    return Path(root) / ".mengram" / FILE


def load(root) -> list[dict]:
    try:
        data = json.loads(path(root).read_text(encoding="utf-8"))
    except Exception:
        return []
    return [i for i in data if isinstance(i, dict) and i.get("tool_use_id")] \
        if isinstance(data, list) else []


def save(root, intents: list[dict]) -> None:
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    keep = [i for i in intents if now - float(i.get("opened_at") or now) < MAX_AGE_SECONDS]
    keep = keep[-MAX_OPEN:]
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(keep, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def open_intent(root, *, tool_use_id: str, procedure: str, step: int, command: str,
                session_id: str | None = None, transcript_path: str | None = None,
                offset: int = 0) -> dict:
    """Record that `command` is about to run as `step` of `procedure`.

    `offset` is where the transcript ended when the intent was opened, so the
    result can later be found without reading the whole session.
    """
    intent = {
        "tool_use_id": tool_use_id,
        "procedure": procedure,
        "step": int(step),
        "command": (command or "")[:500],
        "session_id": session_id,
        "transcript_path": transcript_path,
        "offset": int(offset or 0),
        "opened_at": time.time(),
    }
    intents = [i for i in load(root) if i.get("tool_use_id") != tool_use_id]
    intents.append(intent)
    save(root, intents)
    return intent


def close_intent(root, tool_use_id: str) -> dict | None:
    """Remove and return the intent for this call, or None if there is none."""
    intents = load(root)
    found = None
    rest = []
    for i in intents:
        if i.get("tool_use_id") == tool_use_id and found is None:
            found = i
        else:
            rest.append(i)
    if found is not None:
        save(root, rest)
    return found


def open_intents(root) -> list[dict]:
    return load(root)
