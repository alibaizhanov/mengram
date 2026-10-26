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
for commands that matched a step, which is a few in a hundred. Parallel tool
calls open and close intents at the same moment, so every change is a
read-modify-write under `.mengram/intents.lock`: without it one call's save
put back an intent another had just closed, and its failure was charged twice.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .persistence import atomic_write, file_lock

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


def lock(root):
    """The lock every read-modify-write of the intents file holds."""
    return file_lock(Path(root) / ".mengram" / "intents.lock")


def save(root, intents: list[dict]) -> None:
    """Write the list. Callers changing it hold `lock(root)` around load+save."""
    now = time.time()
    keep = [i for i in intents if now - float(i.get("opened_at") or now) < MAX_AGE_SECONDS]
    keep = keep[-MAX_OPEN:]
    # A unique temporary per write: a shared `.json.tmp` let one writer's
    # os.replace move another's half-written file, or find it already gone.
    atomic_write(path(root), json.dumps(keep, indent=2) + "\n")


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
    with lock(root):
        intents = [i for i in load(root) if i.get("tool_use_id") != tool_use_id]
        intents.append(intent)
        save(root, intents)
    return intent


def close_intent(root, tool_use_id: str) -> dict | None:
    """Remove and return the intent for this call, or None if there is none.

    Under the lock, so of two callers closing the same intent exactly one
    gets it back: whoever charges a step must be the one that closed it.
    """
    with lock(root):
        found = None
        rest = []
        for i in load(root):
            if i.get("tool_use_id") == tool_use_id and found is None:
                found = i
            else:
                rest.append(i)
        if found is not None:
            save(root, rest)
    return found


def open_intents(root) -> list[dict]:
    return load(root)
