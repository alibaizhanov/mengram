"""`mengram resume`: the task card that follows a task across sessions and agents.

The compaction checkpoint (local/checkpoint.py) answers "what was I doing a
minute ago" for one session on one machine. This answers "where does this
task stand" for whoever picks it up next: tomorrow's session, another agent
in another Orca worktree, the same person on another laptop. So the card is
keyed by the repository and the branch (`git remote` + branch), not by the
directory, and it says on which commit its last check was run, so a reader
can tell a verified state from a stale one.

A card has two kinds of content and says which is which:

- what the transcript shows (files edited, commands run, the last test
  result, HEAD at the time): recorded, never rewritten;
- what an agent drafted from it (the task in one line, what is done, what
  remains): marked `draft` until a person confirms it on the page. An agent's
  proposal is not a decision.

Local only, like the checkpoint: works with no account and through an
outage. The draft needs a model, so it is only added when a cloud key exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from local import checkpoint

MAX_AGE = 14 * 86400          # a card older than this is not injected
DRAFT_MIN_INTERVAL = 300      # seconds between model drafts of the same card
TEST_COMMANDS = re.compile(r"\b(pytest|npm (run )?test|pnpm test|yarn test|go test|cargo test|jest|vitest|"
                           r"make test|python3? -m unittest|rspec|phpunit|mvn test|gradle test)\b")
TEST_SUMMARY = re.compile(r"(\d+ passed[^\n]{0,80}|\d+ failed[^\n]{0,80}|\d+ errors?[^\n]{0,60}|"
                          r"\bFAILED\b[^\n]{0,80}|\bPASS(ED)?\b[^\n]{0,60}|\bok\b[^\n]{0,60}|Tests:[^\n]{0,80})")


def directory() -> Path:
    home = os.environ.get("MENGRAM_HOME")
    return (Path(home) if home else Path.home() / ".mengram") / "resume"


# --- git ----------------------------------------------------------------------------

def _git(cwd, *args, timeout=3) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _norm_remote(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    u = re.sub(r"^git@([^:]+):", r"\1/", u)
    u = re.sub(r"^(https?|ssh)://", "", u)
    u = re.sub(r"^[^@]+@", "", u)
    u = re.sub(r"\.git$", "", u)
    return u.lower().rstrip("/")


def repo_info(cwd) -> dict:
    """remote, branch, root, head, dirty — from git; falls back to the path."""
    cwd = str(cwd or os.getcwd())
    root = _git(cwd, "rev-parse", "--show-toplevel")
    if not root:
        return {"remote": None, "branch": None, "root": cwd, "head": None, "dirty": None}
    remote = _norm_remote(_git(cwd, "config", "--get", "remote.origin.url"))
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    head = _git(cwd, "rev-parse", "--short=10", "HEAD")
    status = _git(cwd, "status", "--porcelain")
    return {"remote": remote, "branch": branch, "root": root, "head": head,
            "dirty": bool(status) if status is not None else None}


def card_key(info: dict) -> str:
    base = f"{info.get('remote') or info.get('root')}#{info.get('branch') or ''}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def commits_since(cwd, old_head: str | None, new_head: str | None) -> int | None:
    if not old_head or not new_head or old_head == new_head:
        return 0 if old_head and old_head == new_head else None
    n = _git(cwd, "rev-list", "--count", f"{old_head}..{new_head}")
    try:
        return int(n) if n is not None else None
    except ValueError:
        return None


# --- what the transcript shows -------------------------------------------------------

_REDIRECT = re.compile(r"(?:>>?|tee(?: -a)?)\s*['\"]?([~./\w][\w./~-]*\.[A-Za-z0-9]{1,8})")
_SED_INPLACE = re.compile(r"\bsed\s+-i(?:\s+'')?\s+(?:-E\s+)?'[^']*'\s+([~./\w][\w./~-]+)")


def files_from_commands(commands: list[str]) -> list[str]:
    """Files a shell command wrote: `cat > path`, `>> path`, `tee path`, `sed -i … path`.
    Edits made through the shell are edits too (this module was written that way)."""
    out: list[str] = []
    for cmd in commands or []:
        for rx in (_REDIRECT, _SED_INPLACE):
            for m in rx.finditer(cmd):
                f = m.group(1)
                if f in ("/dev/null",) or f.startswith("/tmp") or f.startswith("/private/tmp"):
                    continue
                if f not in out:
                    out.append(f)
    return out


def last_test_result(transcript_path) -> dict | None:
    """The last test command in the transcript tail and the summary line of
    its result, if the tool result carried one."""
    try:
        entries = list(checkpoint._entries(transcript_path))
    except Exception:
        return None
    runs: list[dict] = []   # every test command in the tail, with its result if any
    for entry in entries:
        role, content = checkpoint._message(entry)
        if role == "assistant":
            for name, tool_input in checkpoint._tool_uses(content):
                if name in checkpoint.SHELL_TOOLS:
                    cmd = checkpoint._shell_command(tool_input) or ""
                    if TEST_COMMANDS.search(cmd):
                        runs.append({"command": cmd[:200], "result": None})
        elif role == "user" and runs and runs[-1]["result"] is None:
            text = _tool_result_text(content)
            m = TEST_SUMMARY.search(text or "")
            if m:
                runs[-1]["result"] = m.group(0).strip()[:160]
    if not runs:
        return None
    # the last run with a result beats a run still in flight
    for r in reversed(runs):
        if r["result"]:
            return r
    return runs[-1]


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        if isinstance(item, dict):
            if item.get("type") == "tool_result":
                inner = item.get("content")
                parts.append(inner if isinstance(inner, str) else _tool_result_text(inner))
            elif item.get("type") in checkpoint.TEXT_TYPES:
                parts.append(str(item.get("text", "")))
    return "\n".join(p for p in parts if p)


# --- the card --------------------------------------------------------------------------

def build(transcript_path, session_id: str | None, cwd: str | None, host: str | None = None,
          previous: dict | None = None) -> dict:
    """A card from the transcript tail and git, keeping the previous card's
    confirmed content when the person has confirmed it."""
    snap = checkpoint.snapshot(transcript_path, session_id, cwd=cwd, host=host)
    info = repo_info(cwd)
    test = last_test_result(transcript_path) if transcript_path else None
    card = {
        "key": card_key(info), "remote": info["remote"], "branch": info["branch"], "root": info["root"],
        "session": session_id or "unknown", "host": host or "claude-code", "ts": time.time(),
        "prompts": snap.get("prompts") or [],
        "files": (snap.get("files") or []) + [f for f in files_from_commands(snap.get("commands") or [])
                                              if f not in (snap.get("files") or [])],
        "commands": snap.get("commands") or [], "last_assistant": snap.get("last_assistant") or "",
        "head": info["head"], "dirty": info["dirty"],
        "last_check": ({"command": test["command"], "result": test["result"], "head": info["head"], "ts": time.time()}
                       if test else (previous or {}).get("last_check")),
        "task": "", "done": [], "remaining": [], "draft": True, "confirmed_ts": None,
        "draft_hash": None, "draft_ts": None,
    }
    if previous:
        for k in ("task", "done", "remaining", "draft", "confirmed_ts", "draft_hash", "draft_ts"):
            if previous.get(k) not in (None, "", []):
                card[k] = previous[k]
        # the person's confirmed text survives; the agent's draft is refreshed by draft()
        if previous.get("confirmed_ts") and previous.get("draft") is False:
            card["draft"] = False
    return card


def content_hash(card: dict) -> str:
    raw = json.dumps([card.get("prompts"), card.get("files"), card.get("commands"),
                      (card.get("last_check") or {}).get("result")], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def needs_draft(card: dict, now: float | None = None) -> bool:
    """A model draft is worth one call when the recorded content changed and
    the last draft is not minutes old. Confirmed cards are never redrafted."""
    if card.get("confirmed_ts") and card.get("draft") is False:
        return False
    now = now or time.time()
    if card.get("draft_hash") == content_hash(card):
        return False
    return not card.get("draft_ts") or now - float(card["draft_ts"]) >= DRAFT_MIN_INTERVAL


def draft_payload(card: dict) -> dict:
    return {"prompts": card.get("prompts"), "files": card.get("files"), "commands": card.get("commands"),
            "last_assistant": card.get("last_assistant"), "last_check": card.get("last_check"),
            "branch": card.get("branch"), "previous": {"task": card.get("task"), "done": card.get("done"),
                                                       "remaining": card.get("remaining")}}


def apply_draft(card: dict, drafted: dict | None) -> dict:
    """Take the model's task/done/remaining. Marked as a draft; the card keeps
    the record it was drafted from."""
    if not drafted:
        return card
    task = str(drafted.get("task") or "").strip()[:200]
    done = [str(x).strip()[:200] for x in (drafted.get("done") or []) if str(x).strip()][:8]
    remaining = [str(x).strip()[:200] for x in (drafted.get("remaining") or []) if str(x).strip()][:8]
    if task or done or remaining:
        card.update({"task": task or card.get("task", ""), "done": done or card.get("done", []),
                     "remaining": remaining or card.get("remaining", []),
                     "draft": True, "draft_hash": content_hash(card), "draft_ts": time.time()})
    return card


def is_empty(card: dict) -> bool:
    return not (card.get("files") or card.get("commands") or card.get("task") or card.get("prompts"))


# --- storing ------------------------------------------------------------------------------

def path_for(key: str) -> Path:
    return directory() / f"{key}.json"


def save(card: dict) -> Path | None:
    try:
        d = directory(); d.mkdir(parents=True, exist_ok=True)
        p = path_for(card["key"]); tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        return p
    except Exception:
        return None


def load(key: str) -> dict | None:
    try:
        return json.loads(path_for(key).read_text(encoding="utf-8"))
    except Exception:
        return None


def all_cards() -> list[dict]:
    out = []
    try:
        for p in directory().glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    except Exception:
        pass
    return sorted(out, key=lambda c: c.get("ts") or 0, reverse=True)


def load_for(cwd, max_age: float = MAX_AGE) -> tuple[dict | None, str]:
    """The card for this repo and branch; else the newest for the same remote
    (another branch, another worktree), said so. ("", exact, other-branch, none)"""
    info = repo_info(cwd)
    now = time.time()
    card = load(card_key(info))
    if card and now - float(card.get("ts") or 0) <= max_age:
        return card, "exact"
    if info.get("remote"):
        for c in all_cards():
            if c.get("remote") == info["remote"] and now - float(c.get("ts") or 0) <= max_age:
                return c, "other-branch"
    return None, "none"


def confirm(card: dict, task: str | None = None, done: list | None = None, remaining: list | None = None) -> dict:
    """The person's word: what is confirmed stops being a draft."""
    if task is not None:
        card["task"] = task.strip()[:200]
    if done is not None:
        card["done"] = [d.strip()[:200] for d in done if d.strip()][:12]
    if remaining is not None:
        card["remaining"] = [r.strip()[:200] for r in remaining if r.strip()][:12]
    card["draft"] = False
    card["confirmed_ts"] = time.time()
    card["draft_hash"] = content_hash(card)
    return card


# --- rendering -------------------------------------------------------------------------

def _age(ts) -> str:
    try:
        s = max(0, time.time() - float(ts))
    except Exception:
        return "unknown age"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} d ago"


def staleness(card: dict, cwd) -> str | None:
    """Whether the last check still describes the code that is here."""
    lc = card.get("last_check") or {}
    if not lc.get("head"):
        return None
    info = repo_info(cwd)
    if not info.get("head"):
        return None
    if info["head"] == lc["head"]:
        return f"The last check ran on this exact commit ({lc['head']})." + (" Working tree has uncommitted changes." if info.get("dirty") else "")
    n = commits_since(cwd, lc["head"], info["head"])
    later = f"{n} commit(s) later" if n is not None else "a different commit"
    return f"The last check ran on {lc['head']}; HEAD is now {info['head']} ({later}). Its result describes the earlier state."


def render(card: dict, cwd=None, relation: str = "exact") -> str:
    """The block a starting session gets. Labelled, short, honest about drafts."""
    who = card.get("host") or "an agent"
    head = [f"[Mengram resume — where this task stands ({_age(card.get('ts'))}, from {who}"]
    if card.get("branch"):
        head[0] += f", branch {card['branch']}"
    head[0] += ")]"
    if relation == "other-branch":
        head.append(f"(No card for this branch; this is the newest card for the same repository, branch {card.get('branch')}.)")
    lines = head
    if card.get("task"):
        lines.append(f"Task{' (agent draft, not confirmed)' if card.get('draft') else ''}: {card['task']}")
    if card.get("done"):
        lines.append("Done:")
        lines += [f"  - {d}" for d in card["done"]]
    if card.get("remaining"):
        lines.append("Remaining:")
        lines += [f"  - {r}" for r in card["remaining"]]
    lc = card.get("last_check") or {}
    if lc.get("command"):
        lines.append(f"Last check: `{lc['command']}` → {lc.get('result') or 'result not captured'}")
        st = staleness(card, cwd) if cwd else None
        if st:
            lines.append(f"  {st}")
    if card.get("files"):
        lines.append("Files touched:")
        lines += [f"  - {f}" for f in card["files"][-10:]]
    if card.get("prompts"):
        lines.append(f"Last request: «{card['prompts'][-1][:200]}»")
    src = [f"session {card.get('session')}"]
    if card.get("head"):
        src.append(f"commit {card['head']}")
    lines.append("Sources: " + " · ".join(src))
    if card.get("draft") and (card.get("task") or card.get("done")):
        lines.append("Treat task/done/remaining as the previous agent's reading, not as decisions; the record above it is verbatim.")
    return "\n".join(lines)


def headline(card: dict, relation: str = "exact") -> str:
    parts = []
    if card.get("task"):
        parts.append(f"task «{card['task'][:80]}»")
    if card.get("remaining"):
        parts.append(f"{len(card['remaining'])} step(s) remaining")
    lc = card.get("last_check") or {}
    if lc.get("result"):
        parts.append(f"last check: {lc['result'][:60]}")
    what = ", ".join(parts) if parts else f"{len(card.get('files') or [])} file(s) touched"
    where = f" (from branch {card.get('branch')})" if relation == "other-branch" else ""
    return f"🧠 Mengram resume: {what}{where}. `mengram resume --open` to correct it."
