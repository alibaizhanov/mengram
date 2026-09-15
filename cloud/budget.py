"""A recall that fits: `max_tokens` on search, and what was cut, said.

The integrator's complaint is not that memory is wrong but that it is big: a
bot for a car-rental company was sending 4,500–5,000 tokens of history on
every request just to remember a customer (n8n community, 2026-09). Search
already ranks; what it lacked was a stop. `max_tokens` is that stop — the
caller says how much room the reply may take, and the reply is cut in rank
order to fit, entity by entity, fact by fact, with a report of what was left
out so the cut is never silent.

Tokens are estimated, not tokenized: the server does not know which model
will read the reply. About four characters per token for Latin text and
about two and a half for everything else is within the spread between
tokenizers and errs on the safe side. The report says `"method": "estimate"`
so nobody mistakes it for a count.
"""

from __future__ import annotations

import math

#: Bounds for `max_tokens`. Below 50 nothing useful fits; above this the
#: caller is not asking for a budget.
MIN_BUDGET = 50
MAX_BUDGET = 50_000


def estimate_tokens(text) -> int:
    """Rough token count for any string; 0 for nothing."""
    if not text:
        return 0
    s = str(text)
    non_ascii = sum(1 for c in s if ord(c) > 127)
    return max(1, math.ceil((len(s) - non_ascii) / 4 + non_ascii / 2.5))


class _Budget:
    def __init__(self, max_tokens: int):
        self.max = int(max_tokens)
        self.used = 0

    def take(self, text) -> bool:
        """Spend the tokens for `text` if they fit; False and unchanged if not."""
        cost = estimate_tokens(text)
        if self.used + cost > self.max:
            return False
        self.used += cost
        return True


def _relation_text(rel) -> str:
    if isinstance(rel, dict):
        return f"{rel.get('type', '')} → {rel.get('target', '')}"
    return str(rel)


def _knowledge_text(k) -> str:
    if isinstance(k, dict):
        return f"{k.get('title', '')}: {k.get('content', '')}"
    return str(k)


def fit_entities(results: list[dict], budget: _Budget, report: dict) -> list[dict]:
    """Keep entities in rank order while they fit. Within an entity: the
    header, then facts in order, then relations, then knowledge. The first
    thing that does not fit ends the reply — a later, lower-ranked entity
    must not slip in because it happens to be short."""
    kept = []
    for r in results:
        header = f"{r.get('entity', '')} ({r.get('type', '')})"
        if not budget.take(header):
            report["dropped"]["entities"] += 1
            report["dropped"]["facts"] += len(r.get("facts") or [])
            break
        out = dict(r)
        facts, dropped_facts = [], 0
        stopped = False
        for f in r.get("facts") or []:
            if stopped or not budget.take(f):
                stopped = True
                dropped_facts += 1
                continue
            facts.append(f)
        out["facts"] = facts
        rels, dropped_rels = [], 0
        for rel in r.get("relations") or []:
            if stopped or not budget.take(_relation_text(rel)):
                stopped = True
                dropped_rels += 1
                continue
            rels.append(rel)
        out["relations"] = rels
        know, dropped_know = [], 0
        for k in r.get("knowledge") or []:
            if stopped or not budget.take(_knowledge_text(k)):
                stopped = True
                dropped_know += 1
                continue
            know.append(k)
        out["knowledge"] = know
        kept.append(out)
        report["kept"]["entities"] += 1
        report["kept"]["facts"] += len(facts)
        report["dropped"]["facts"] += dropped_facts
        report["dropped"]["relations"] += dropped_rels
        report["dropped"]["knowledge"] += dropped_know
        if stopped:
            rest = results[results.index(r) + 1:]
            report["dropped"]["entities"] += len(rest)
            report["dropped"]["facts"] += sum(len(x.get("facts") or []) for x in rest)
            break
    return kept


def _fit_units(items: list[dict], text_of, budget: _Budget, report: dict, kind: str) -> list[dict]:
    kept = []
    for i, item in enumerate(items):
        if not budget.take(text_of(item)):
            report["dropped"][kind] += len(items) - i
            break
        kept.append(item)
        report["kept"][kind] += 1
    return kept


def _episode_text(ep) -> str:
    s = ep.get("summary", "")
    if ep.get("outcome"):
        s += f" → {ep['outcome']}"
    return s


def _procedure_text(p) -> str:
    steps = p.get("steps") or []
    return p.get("name", "") + " " + " ".join(
        f"{s.get('step', '')}. {s.get('action', '')} {s.get('detail', '')}" if isinstance(s, dict) else str(s)
        for s in steps)


def _new_report(max_tokens: int) -> dict:
    return {
        "max_tokens": int(max_tokens),
        "used_tokens": 0,
        "method": "estimate",
        "kept": {"entities": 0, "facts": 0, "episodes": 0, "procedures": 0, "chunks": 0},
        "dropped": {"entities": 0, "facts": 0, "relations": 0, "knowledge": 0,
                    "episodes": 0, "procedures": 0, "chunks": 0},
    }


def fit_search(results: list[dict], max_tokens: int) -> tuple[list[dict], dict]:
    """`/v1/search`: entities cut to the budget, plus the report."""
    budget = _Budget(max_tokens)
    report = _new_report(max_tokens)
    kept = fit_entities(results, budget, report)
    report["used_tokens"] = budget.used
    return kept, report


def fit_search_all(result: dict, max_tokens: int) -> tuple[dict, dict]:
    """`/v1/search/all`: one budget across the sections, spent in the order a
    task needs them — facts about the entities first, then what happened,
    then how it was done, then raw conversation last. `results` (the merged
    ranking) is rebuilt from what survived so the two views agree."""
    budget = _Budget(max_tokens)
    report = _new_report(max_tokens)
    out = dict(result)
    out["semantic"] = fit_entities(result.get("semantic") or [], budget, report)
    out["episodic"] = _fit_units(result.get("episodic") or [], _episode_text, budget, report, "episodes")
    out["procedural"] = _fit_units(result.get("procedural") or [], _procedure_text, budget, report, "procedures")
    out["chunks"] = _fit_units(result.get("chunks") or [], lambda c: c.get("content", "") or c.get("text", ""),
                               budget, report, "chunks")
    survivors = {id(x) for sec in ("semantic", "episodic", "procedural", "chunks") for x in out[sec]}
    kept_keys = set()
    for sec in ("semantic", "episodic", "procedural", "chunks"):
        for x in out[sec]:
            kept_keys.add(_identity(x))
    out["results"] = [x for x in (result.get("results") or []) if _identity(x) in kept_keys]
    report["used_tokens"] = budget.used
    return out, report


def _identity(x: dict):
    """What names an item across the merged list and its section: entity
    name, episode summary, procedure name or chunk text."""
    for key in ("entity", "summary", "name", "content", "text"):
        if x.get(key):
            return (key, x[key])
    return ("id", id(x))
