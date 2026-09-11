"""Failures, read out of the session transcript.

PostToolUse does not fire when a Bash command fails. The event arrives only
after a command that succeeded, verified with a logging hook: a marker command
exiting 7 produced no event at all. That leaves the transcript as the only
place a failure is written down.

It matters more than it sounds. A workflow's trust is computed from its
weakest watched step, so a record built only out of successes climbs steadily
until the gate falls silent — for a workflow that may have been breaking all
week. Half the evidence is worse than none, because it is confidently wrong.

The transcript is append-only JSON lines. A failed Bash call appears as a
`tool_result` carrying `is_error` and a body that starts with `Exit code N`,
pointing back by id at the `tool_use` that holds the command.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

CURSOR_FILE = "outcome-cursor.json"

#: Ids kept to stop a re-read from charging the same step twice. Bash failures
#: are rare — three in a twelve-thousand-line session — so this never fills.
MAX_SEEN = 1000

#: How far back to start reading before the stored offset. A `tool_use` and its
#: result sit within a few lines of each other, but a cursor can land between
#: them, and a failure whose command cannot be resolved is a failure lost.
LOOKBACK_BYTES = 64 * 1024

_EXIT_CODE = re.compile(r"^Exit code (\d+)\s*", re.DOTALL)


def cursor_path(root) -> Path:
    return Path(root) / ".mengram" / CURSOR_FILE


def read_cursor(root) -> dict:
    """`{"offset": int, "seen": [tool_use_id]}` — empty when there is none."""
    try:
        return json.loads(cursor_path(root).read_text())
    except Exception:
        return {}


def write_cursor(root, cursor: dict) -> None:
    p = cursor_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    cursor = dict(cursor)
    cursor["seen"] = list(cursor.get("seen") or [])[-MAX_SEEN:]
    p.write_text(json.dumps(cursor, indent=2) + "\n", encoding="utf-8")


def _reason(body: str) -> str | None:
    """The shortest honest description of what went wrong."""
    text = _EXIT_CODE.sub("", (body or "").strip()).strip()
    if not text:
        return None
    return text.splitlines()[-1].strip()[:200] or None


def _body(content) -> str:
    """A tool result's text, whether the host wrote a string or a list of blocks."""
    if isinstance(content, list):
        return " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
    return str(content or "")


def ran_and_failed(body: str) -> bool:
    """Did this error result come from a command that ran and exited non-zero?

    Only a body that begins `Exit code N` did. A host writes `is_error` on a
    great deal else: a permission the classifier refused, a push a branch rule
    blocked, the user declining at the prompt, a call that timed out. Measured
    on one real project: 14 refusals and 12 blocked pushes against 7 genuine
    exits. None of those commands ran, so none of them is evidence about the
    step — and each one, counted, would have cost a workflow its trust.
    """
    return bool(_EXIT_CODE.match((body or "").lstrip()))


def classify(is_error, body: str) -> str:
    """`"ok"`, `"failed"` (ran, non-zero exit) or `"not_run"` (refused, declined, timed out)."""
    if not is_error:
        return "ok"
    return "failed" if ran_and_failed(body) else "not_run"


def _scan(transcript_path, start: int):
    """`({tool_use_id: command}, [(tool_use_id, body)])` for failures from `start`.

    Only commands that ran and exited non-zero are failures here; see
    `ran_and_failed` for what an error result can otherwise be.
    """
    commands: dict[str, str] = {}
    errors: list[tuple[str, str]] = []
    try:
        with open(transcript_path, "r", errors="ignore") as fh:
            fh.seek(start)
            if start:
                fh.readline()            # the seek landed mid-line
            for line in fh:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                content = (entry.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("name") == "Bash":
                        cmd = (block.get("input") or {}).get("command")
                        if cmd:
                            commands[block.get("id")] = cmd
                    elif block.get("type") == "tool_result" and block.get("is_error"):
                        body = _body(block.get("content"))
                        if ran_and_failed(body):
                            errors.append((block.get("tool_use_id"), body))
    except OSError:
        return {}, []
    return commands, errors


def result_for(transcript_path, tool_use_id: str, start: int = 0) -> tuple[str, str] | None:
    """The result the transcript holds for one tool call: `(kind, body)`.

    `kind` is what `classify` returns. None when there is no result yet — the
    command may still be running, or the session ended before it finished.
    Reads from a little before `start`, the transcript's size when the call
    was noted, so a long session is not re-read for every lookup.
    """
    if not tool_use_id:
        return None
    try:
        with open(transcript_path, "r", errors="ignore") as fh:
            begin = max(0, int(start or 0) - LOOKBACK_BYTES)
            fh.seek(begin)
            if begin:
                fh.readline()
            for line in fh:
                if tool_use_id not in line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                content = (entry.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if (isinstance(block, dict) and block.get("type") == "tool_result"
                            and block.get("tool_use_id") == tool_use_id):
                        body = _body(block.get("content"))
                        return classify(block.get("is_error"), body), body
    except OSError:
        return None
    return None


def new_failures(transcript_path, cursor: dict) -> tuple[list[tuple[str, str | None]], dict]:
    """Bash commands that failed since the cursor: `([(command, reason)], cursor)`.

    A first run records nothing and marks the current end of the file. Reaching
    back through a session's history to charge steps for failures from days ago
    would be a surprise, and a loud one.

    It also has to mark what is already within reach of the lookback as seen.
    Without that, the very next read reaches back past the cursor and charges
    for exactly the history the first run refused to touch.
    """
    try:
        size = os.path.getsize(transcript_path)
    except OSError:
        return [], cursor

    seen = list(cursor.get("seen") or [])

    if "offset" not in cursor:
        _, errors = _scan(transcript_path, max(0, size - LOOKBACK_BYTES))
        seen.extend(tid for tid, _ in errors if tid)
        return [], {"offset": size, "seen": seen}

    start = cursor["offset"]
    if start > size:                     # the file was replaced, not appended to
        start = 0
    commands, errors = _scan(transcript_path, max(0, start - LOOKBACK_BYTES))

    seen_set = set(seen)
    failures: list[tuple[str, str | None]] = []
    for tid, body in errors:
        if not tid or tid in seen_set or tid not in commands:
            continue
        seen_set.add(tid)
        seen.append(tid)
        failures.append((commands[tid], _reason(body)))

    return failures, {"offset": size, "seen": seen}
