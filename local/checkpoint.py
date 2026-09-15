"""The working state, written down before compaction and read back after.

Compaction keeps the conversation but not the work. What survives is a
summary the host wrote for itself: which files were open, which command last
failed, what the person asked for three prompts ago — any of it can drop out,
and the agent carries on from the summary as if nothing were missing. The
person notices later, when a file is edited from a stale idea of it or a
finished step is done twice.

PreCompact fires before the summary is written and hands over the transcript
path. This module reads the transcript's tail, keeps the few things that
locate the work — the last prompts, the files touched, the last commands, the
last thing the agent said — and writes them under the session id. SessionStart
with `source: compact` (or `resume`) reads them back into the context, exact,
next to whatever the host's summary kept. Nothing is extracted or rewritten:
the value is that the record is verbatim.

Kept in `~/.mengram/checkpoints/<session_id>.json`. Same file layout for Claude
Code and Codex; the transcript reader accepts both hosts' JSON lines and takes
what it recognises, so an unfamiliar line costs nothing.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

DIR = "checkpoints"

#: How much of the transcript's end to read. A compaction happens on a long
#: session; the work that matters is at the end of it.
TAIL_BYTES = 512 * 1024

#: Files under `checkpoints/` past which the oldest are dropped.
MAX_FILES = 60

#: How old a checkpoint may be and still be offered to a `resume`.
RESUME_MAX_AGE = 7 * 86400

MAX_PROMPTS = 4
MAX_FILES_TOUCHED = 20
MAX_COMMANDS = 8
MAX_ASSISTANT_CHARS = 1500
MAX_PROMPT_CHARS = 400

#: Tool names whose input names a file the agent changed.
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch",
              "edit_file", "write_file", "create_file"}
FILE_KEYS = ("file_path", "notebook_path", "path", "filePath")
SHELL_TOOLS = {"Bash", "shell", "local_shell", "exec_command", "container.exec"}


def directory() -> Path:
    return Path(os.environ.get("MENGRAM_HOME") or (Path.home() / ".mengram")) / DIR


def path_for(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)[:120]
    return directory() / f"{safe or 'unknown'}.json"


# --- reading the transcript ---------------------------------------------------

def _text_of(content) -> str:
    """The human-readable text in a message's content, tool blocks left out."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def _is_tool_result_only(content) -> bool:
    return (isinstance(content, list) and content
            and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content))


def _tool_uses(content):
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block.get("name") or "", block.get("input") or {}


def _shell_command(tool_input) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command") or tool_input.get("cmd")
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    return str(cmd) if cmd else None


def _file_of(tool_input) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in FILE_KEYS:
        v = tool_input.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _entries(transcript_path, tail_bytes: int = TAIL_BYTES):
    """JSON lines from the end of the transcript, oldest first."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "r", errors="ignore") as fh:
            begin = max(0, size - tail_bytes)
            fh.seek(begin)
            if begin:
                fh.readline()
            for line in fh:
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


def _message(entry) -> tuple[str | None, object]:
    """`(role, content)` for a line, whichever host wrote it. None when the
    line is not a message (a progress event, a summary, a Codex state line)."""
    if not isinstance(entry, dict):
        return None, None
    # Claude Code: {"type": "user"|"assistant", "message": {"role", "content"}}
    msg = entry.get("message")
    if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
        return msg.get("role"), msg.get("content")
    # Codex rollout: {"type": "response_item", "payload": {"type": "message", "role", "content"}}
    payload = entry.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") == "message" and payload.get("role") in ("user", "assistant"):
            content = payload.get("content")
            if isinstance(content, list):
                content = [{"type": "text", "text": b.get("text", "")}
                           for b in content if isinstance(b, dict) and "text" in b]
            return payload.get("role"), content
        if payload.get("type") == "function_call":
            # A Codex tool call: shell commands and file edits arrive here.
            name = payload.get("name") or ""
            try:
                args = json.loads(payload.get("arguments") or "{}")
            except Exception:
                args = {}
            return "assistant", [{"type": "tool_use", "name": name, "input": args}]
    return None, None


def snapshot(transcript_path, session_id: str | None, cwd: str | None = None,
             trigger: str | None = None, host: str | None = None) -> dict:
    """What the session was doing, read from the transcript's tail."""
    prompts: list[str] = []
    files: list[str] = []
    commands: list[str] = []
    last_assistant = ""

    for entry in _entries(transcript_path):
        role, content = _message(entry)
        if role is None:
            continue
        if role == "user":
            if _is_tool_result_only(content):
                continue
            text = _text_of(content).strip()
            if text and not text.startswith("<"):   # hook/system wrappers, not the person
                prompts.append(text[:MAX_PROMPT_CHARS])
        elif role == "assistant":
            text = _text_of(content).strip()
            if text:
                last_assistant = text
            for name, tool_input in _tool_uses(content):
                if name in EDIT_TOOLS:
                    f = _file_of(tool_input)
                    if f and f not in files:
                        files.append(f)
                elif name in SHELL_TOOLS:
                    cmd = _shell_command(tool_input)
                    if cmd:
                        commands.append(cmd[:200])

    return {
        "session": session_id or "unknown",
        "host": host or "claude-code",
        "cwd": cwd,
        "trigger": trigger,
        "ts": time.time(),
        "prompts": prompts[-MAX_PROMPTS:],
        "files": files[-MAX_FILES_TOUCHED:],
        "commands": commands[-MAX_COMMANDS:],
        "last_assistant": last_assistant[-MAX_ASSISTANT_CHARS:],
    }


def is_empty(snap: dict) -> bool:
    return not (snap.get("prompts") or snap.get("files") or snap.get("commands")
                or snap.get("last_assistant"))


# --- storing -------------------------------------------------------------------

def save(snap: dict) -> Path | None:
    """Write the checkpoint under its session id. Never raises."""
    try:
        d = directory()
        d.mkdir(parents=True, exist_ok=True)
        p = path_for(snap.get("session") or "unknown")
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        _prune(d)
        return p
    except Exception:
        return None


def _prune(d: Path) -> None:
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for p in files[:-MAX_FILES]:
        try:
            p.unlink()
        except OSError:
            pass


def load(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    try:
        return json.loads(path_for(session_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def latest_for(cwd: str | None, max_age: float = RESUME_MAX_AGE) -> dict | None:
    """The newest checkpoint written from this directory, for a resumed
    session whose id the host may have changed."""
    if not cwd:
        return None
    best = None
    now = time.time()
    try:
        for p in directory().glob("*.json"):
            try:
                snap = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            ts = snap.get("ts")
            if snap.get("cwd") != cwd or not isinstance(ts, (int, float)):
                continue
            if now - ts > max_age:
                continue
            if best is None or ts > best.get("ts", 0):
                best = snap
    except OSError:
        return None
    return best


def consume(session_id: str | None, cwd: str | None = None) -> dict | None:
    """The checkpoint a starting session should see: its own, else the newest
    from the same directory. Restored once — the file is removed so a later
    `/clear` in the same session does not replay old work."""
    snap = load(session_id) or latest_for(cwd)
    if snap is None:
        return None
    try:
        path_for(snap.get("session") or "unknown").unlink()
    except OSError:
        pass
    return snap


# --- rendering -----------------------------------------------------------------

def _age(ts) -> str:
    if not isinstance(ts, (int, float)):
        return ""
    m = int((time.time() - ts) // 60)
    if m < 1:
        return "just now"
    if m < 60:
        return f"{m} min ago"
    h = m // 60
    return f"{h} h ago" if h < 48 else f"{h // 24} d ago"


def headline(snap: dict) -> str:
    """One line for the person, shown the moment the state comes back. The
    restore is otherwise invisible — the block goes into the model's context,
    and the person only notices when the agent *doesn't* redo finished work.
    Said out loud, it is the moment they can point at."""
    files = snap.get("files") or []
    prompts = snap.get("prompts") or []
    commands = snap.get("commands") or []
    parts = []
    if files:
        parts.append(f"{len(files)} file{'s' if len(files) != 1 else ''} you edited")
    if prompts:
        last = prompts[-1].replace("\n", " ").strip()
        if len(last) > 70:
            last = last[:67].rstrip() + "…"
        parts.append(f"your last request («{last}»)")
    if commands:
        parts.append("the last commands run")
    what = ", ".join(parts) if parts else "where you left off"
    when = "compaction" if snap.get("trigger") in ("auto", "manual") else "the break"
    return f"🧠 Mengram put the working state back after {when}: {what}. The summary may have dropped it; this is exact."


def render(snap: dict) -> str:
    """The block SessionStart adds to the context. Verbatim, labelled, short."""
    lines = ["[Mengram — working state saved before compaction "
             f"({_age(snap.get('ts'))}; the host's summary may have dropped some of this)]"]
    if snap.get("cwd"):
        lines.append(f"Directory: {snap['cwd']}")
    if snap.get("prompts"):
        lines.append("Last things the user asked, oldest first:")
        for p in snap["prompts"]:
            lines.append("  - " + p.replace("\n", " ").strip())
    if snap.get("files"):
        lines.append("Files edited this session (re-read before editing again):")
        for f in snap["files"]:
            lines.append("  - " + f)
    if snap.get("commands"):
        lines.append("Last commands run:")
        for c in snap["commands"]:
            lines.append("  $ " + c.replace("\n", " ").strip())
    if snap.get("last_assistant"):
        lines.append("Where the assistant left off:")
        lines.append("  " + snap["last_assistant"].replace("\n", "\n  ").strip())
    return "\n".join(lines)
