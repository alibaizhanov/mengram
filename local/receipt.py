"""What memory did for you, one line per thing, so a session can be summed up.

A hook that works is invisible: the recalled fact goes into the prompt, the
gate's question looks like any permission prompt, the recorded outcome is a
number in a file nobody opens. Nothing here changes what the hooks do. Each
one just leaves a line behind — *recalled on this prompt*, *asked before this
workflow*, *recorded this step* — and the next session start, or
`mengram receipt`, adds those lines up into a sentence.

Kept in `~/.mengram/receipts.jsonl`, whichever mode the hooks run in: the
receipt is about this machine's sessions, not about where the memory lives.
Bounded, append-only, and never a reason for a hook to fail.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

FILE = "receipts.jsonl"

#: Past this size the oldest half is dropped on the next write.
MAX_BYTES = 512 * 1024

#: Event kinds a hook may note. Anything else is ignored on read.
KINDS = ("recall", "gate", "step", "save")


def path() -> Path:
    return Path.home() / ".mengram" / FILE


def note(kind: str, session_id: str | None, **fields) -> None:
    """Append one event. Never raises: this is bookkeeping inside a hook."""
    if kind not in KINDS:
        return
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        event = {"ts": time.time(), "kind": kind, "session": session_id or "unknown"}
        event.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
        if p.stat().st_size > MAX_BYTES:
            _trim(p)
    except Exception:
        return


def _trim(p: Path) -> None:
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    keep = lines[len(lines) // 2:]
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(keep), encoding="utf-8")
    os.replace(tmp, p)


def load() -> list[dict]:
    try:
        text = path().read_text(encoding="utf-8")
    except Exception:
        return []
    events = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if isinstance(e, dict) and e.get("kind") in KINDS:
            events.append(e)
    return events


def summarise(events: list[dict]) -> dict:
    """Counts a person would quote: prompts recalled on, gate questions,
    step outcomes, failures found where the host reported nothing, saves."""
    s = {"recalls": 0, "gates": 0, "steps_ok": 0, "steps_failed": 0,
         "caught": 0, "saves": 0, "sessions": len({e.get("session") for e in events}),
         "first": None, "last": None}
    for e in events:
        k = e.get("kind")
        if k == "recall":
            s["recalls"] += 1
        elif k == "gate":
            s["gates"] += 1
        elif k == "step":
            if e.get("ok"):
                s["steps_ok"] += 1
            else:
                s["steps_failed"] += 1
                if e.get("source") == "transcript":
                    s["caught"] += 1
        elif k == "save":
            s["saves"] += 1
        ts = e.get("ts")
        if isinstance(ts, (int, float)):
            s["first"] = ts if s["first"] is None else min(s["first"], ts)
            s["last"] = ts if s["last"] is None else max(s["last"], ts)
    return s


def phrase(s: dict) -> str | None:
    """One sentence, or None when there is nothing to say. Only what happened
    is listed: a zero is silence, not a reproach."""
    parts = []
    if s["recalls"]:
        parts.append(f"recalled memories on {s['recalls']} prompt{'s' if s['recalls'] != 1 else ''}")
    if s["gates"]:
        parts.append(f"asked before {s['gates']} workflow{'s' if s['gates'] != 1 else ''} with a weak record")
    total = s["steps_ok"] + s["steps_failed"]
    if total:
        parts.append(f"recorded {total} step outcome{'s' if total != 1 else ''} "
                     f"({s['steps_ok']} ok, {s['steps_failed']} failed)")
    if s["caught"]:
        parts.append(f"caught {s['caught']} failure{'s' if s['caught'] != 1 else ''} the host never reported")
    if s["saves"]:
        parts.append(f"saved {s['saves']} turn{'s' if s['saves'] != 1 else ''} to memory")
    return " · ".join(parts) if parts else None


def previous_session(current: str | None, events: list[dict] | None = None) -> tuple[str | None, list[dict]]:
    """The most recent session other than `current`, with its events."""
    events = load() if events is None else events
    order = []
    for e in events:
        sid = e.get("session")
        if sid and sid != current and sid != "unknown" and sid not in order:
            order.append(sid)
    if not order:
        return None, []
    sid = order[-1]
    return sid, [e for e in events if e.get("session") == sid]


def session_line(current: str | None, events: list[dict] | None = None) -> str | None:
    """The line SessionStart shows: what memory did last session, if anything."""
    _sid, evs = previous_session(current, events)
    if not evs:
        return None
    text = phrase(summarise(evs))
    return f"🧠 Mengram, last session: {text}" if text else None


def since(days: int, events: list[dict] | None = None, now: float | None = None) -> list[dict]:
    events = load() if events is None else events
    cutoff = (now or time.time()) - days * 86400
    return [e for e in events if isinstance(e.get("ts"), (int, float)) and e["ts"] >= cutoff]
