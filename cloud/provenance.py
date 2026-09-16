"""Where a fact came from, and which host may see it.

Two people in the r/cursor thread (2026-09-16) asked for the same thing from
different ends: one wanted every recalled fact to say which tool wrote it and
when; the other had a Cursor workspace path from a Mac land in a Claude Code
session on a Linux box, where the agent tried to cd into it. A fact does not
know which tool it came from unless the tool says so at write time, so:

- the hooks send `source` (claude-code / codex / cursor) and a small metadata
  dict (session, cwd, os) with every add; the store keeps it on each fact
  (`facts.metadata`), next to `created_at` and the recall counters;
- search results carry `facts_meta`, one entry per fact, aligned with `facts`:
  {"source", "os", "cwd", "session", "when", "recalled", "last_recalled"};
- a caller that says who it is (`X-Mengram-Host: <os>/<tool>`) does not get
  host-specific facts recorded on another host — paths, tool settings — and the
  reply says how many were left out. Callers that say nothing see everything.

Nothing here rewrites a fact. The tag a renderer shows ("claude-code,
2026-09-15") is built from the metadata at read time.
"""
from __future__ import annotations

import re

HEADER = "X-Mengram-Host"

TOOLS = ("claude-code", "codex", "cursor", "mcp", "api")

#: A fact that only makes sense on the machine or in the tool that wrote it.
_PATH = re.compile(r"(?:^|[\s\"'`(])(?:/Users/|/home/|/opt/|/var/|/tmp/|/mnt/|[A-Za-z]:\\|~/)[^\s\"'`)]*")
_TOOL_FILES = re.compile(r"(?:\.cursor/|\.claude/|\.codex/|\bCLAUDE\.md\b|\bAGENTS\.md\b|\bsettings\.json\b|"
                         r"\bhooks\.json\b|\bcursorrules\b|\.cursorrules\b|\bmcp\.json\b)", re.I)
_TOOL_NAMES = {
    "cursor": re.compile(r"\b(cursor)\b", re.I),
    "claude-code": re.compile(r"\b(claude code|claude-code)\b", re.I),
    "codex": re.compile(r"\b(codex)\b", re.I),
}


def parse_host(value: str | None) -> tuple[str, str] | None:
    """"darwin/claude-code" → ("darwin", "claude-code"). None when absent or odd."""
    if not value:
        return None
    parts = value.strip().lower().split("/", 1)
    os_ = parts[0].strip()
    tool = parts[1].strip() if len(parts) > 1 else ""
    if not os_ or not re.fullmatch(r"[a-z0-9._-]{1,32}", os_) or (tool and not re.fullmatch(r"[a-z0-9._-]{1,32}", tool)):
        return None
    return os_, tool


def host_of(headers) -> tuple[str, str] | None:
    try:
        return parse_host(headers.get(HEADER) or headers.get(HEADER.lower()))
    except Exception:
        return None


def host_specific(fact: str) -> str | None:
    """Why a fact is tied to one host: "path", "tool file", or the tool's
    name; None when it travels."""
    text = fact or ""
    if _TOOL_FILES.search(text):
        return "tool file"
    if _PATH.search(text):
        return "path"
    for tool, rx in _TOOL_NAMES.items():
        if rx.search(text):
            return tool
    return None


def meta_summary(meta: dict | None, created_at=None, access_count=None, last_accessed=None) -> dict:
    """The provenance a reader needs, from what the store has on the row."""
    meta = meta or {}
    out = {
        "source": (meta.get("source") or meta.get("tool") or "api"),
        "os": meta.get("os"),
        "cwd": meta.get("cwd"),
        "session": meta.get("session_id") or meta.get("run_id"),
        "when": _date(created_at),
        "recalled": int(access_count or 0),
        "last_recalled": _date(last_accessed),
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def _date(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "date"):
        return value.date().isoformat()
    s = str(value)
    return s[:10] if len(s) >= 10 else s


def filter_for_host(facts: list[str], metas: list[dict] | None, host: tuple[str, str] | None
                    ) -> tuple[list[str], list[dict], int]:
    """Drop facts recorded on another host that would not make sense on this
    one. Returns (facts, metas, dropped). No host, or no metas: nothing dropped."""
    if not host or not metas or len(metas) != len(facts):
        return facts, list(metas or []), 0
    host_os, host_tool = host
    kept_f, kept_m, dropped = [], [], 0
    for fact, meta in zip(facts, metas):
        why = host_specific(fact)
        # Only paths and tool files are tied to a host; "uses Cursor for
        # front-end work" is a fact about the person and travels everywhere.
        if why in ("path", "tool file"):
            other_os = bool(meta.get("os")) and bool(host_os) and meta["os"] != host_os
            other_tool = why == "tool file" and meta.get("source") in TOOLS and bool(host_tool) \
                and meta["source"] != host_tool
            if other_os or other_tool:
                dropped += 1
                continue
        kept_f.append(fact); kept_m.append(meta)
    return kept_f, kept_m, dropped


def tag(meta: dict | None) -> str:
    """What a renderer appends to a fact: "claude-code, 2026-09-15"."""
    if not meta:
        return ""
    parts = [p for p in (meta.get("source"), meta.get("when")) if p]
    return ", ".join(parts)
