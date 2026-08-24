"""Serialise a user's memory into an Obsidian-native Markdown tree.

The promise this exists to keep: your memory is not locked in our database.
Facts, events and the workflows it learned come out as plain files you can
read, grep, edit and keep after we are gone.

Obsidian-native means relations become `[[wikilinks]]`, so the graph view works
with no configuration — the files are the graph. Every file carries its id in
frontmatter so a future import updates rather than duplicates.

Pure functions: dicts in, `{path: text}` out. No database, no model call, no
filesystem. The API endpoint, the CLI and the plugin all serialise through here
so the three can never drift apart.
"""

from __future__ import annotations

import re
from datetime import date, datetime

#: The format is published as a spec and a library of its own:
#: https://github.com/alibaizhanov/memfmt. These three constants are the whole
#: of what makes a tree readable by it, so they live together and are named
#: after the spec rather than after us — a standard with our product's name in
#: its field keys is not a standard anyone else adopts.
TYPE_KEY = "memfmt_type"
ROOT = "memory"
INDEX = "MEMORY.md"

# Characters a filename cannot carry on the platforms Obsidian runs on. Kept
# deliberately small: entity names are the note titles users will see and link
# by, so we preserve everything we safely can rather than lowercasing or
# stripping punctuation the way a URL slug would.
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SPACES = re.compile(r"\s+")
MAX_STEM = 80


def slugify(name: str) -> str:
    """A filename that survives every filesystem while still reading as the name.

    The original always goes in the `# H1` heading, so nothing is lost when a
    name has to be trimmed here.
    """
    stem = _UNSAFE.sub("-", name or "")
    stem = _SPACES.sub(" ", stem).strip(" .")
    if len(stem) > MAX_STEM:
        stem = stem[:MAX_STEM].rstrip(" .-")
    return stem or "unnamed"


def _unique(stem: str, taken: set) -> str:
    """Two entities can legitimately share a name once punctuation is stripped.
    Rather than let one silently overwrite the other, number the later ones."""
    if stem not in taken:
        taken.add(stem)
        return stem
    n = 2
    while f"{stem} ({n})" in taken:
        n += 1
    stem = f"{stem} ({n})"
    taken.add(stem)
    return stem


def _scalar(value) -> str:
    """One YAML scalar. Quotes only when the value would otherwise be misread —
    unquoted frontmatter is far easier for a human to skim."""
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text != text.strip() or _needs_quotes(text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _needs_quotes(text: str) -> bool:
    if text == "":
        return True
    if text[0] in "-?:,[]{}#&*!|>'\"%@`":
        return True
    return ":" in text or "\n" in text


def frontmatter(fields: dict) -> str:
    """YAML block, skipping empty values so the header stays readable."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {_scalar(v)}" for v in value)
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _day(value) -> str:
    """Just the date part, whatever shape the timestamp arrived in."""
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = str(value or "")
    return text[:10] if len(text) >= 10 else ""


def wikilink(name: str, targets: dict) -> str:
    """A link Obsidian can follow.

    When the file had to be renamed to be safe, the link carries an alias so it
    still reads as the real name: `[[a-b|a/b]]`.
    """
    stem = targets.get(name)
    if stem is None:
        # Not exported (a relation can point outside the current sub-user's
        # slice). Link anyway — Obsidian shows it as an unresolved node, which
        # is honest about there being something there.
        stem = slugify(name)
    return f"[[{stem}]]" if stem == name else f"[[{stem}|{name}]]"


# ---- files ----------------------------------------------------------------

def entity_file(entity: dict, targets: dict) -> str:
    name = entity.get("entity") or "unnamed"
    body = [
        frontmatter({
            TYPE_KEY: "entity",
            "entity_type": entity.get("type"),
            "id": entity.get("id"),
        }),
        "",
        f"# {name}",
    ]

    facts = [f for f in (entity.get("facts") or []) if f]
    if facts:
        body += ["", "## Facts", ""] + [f"- {f}" for f in facts]

    relations = [r for r in (entity.get("relations") or []) if r.get("target")]
    if relations:
        body += ["", "## Relations", ""]
        for r in relations:
            verb = r.get("type") or "related to"
            arrow = "→" if r.get("direction") != "incoming" else "←"
            detail = f" — {r['detail']}" if r.get("detail") else ""
            body.append(f"- {verb} {arrow} {wikilink(r['target'], targets)}{detail}")

    knowledge = [k for k in (entity.get("knowledge") or []) if k.get("content") or k.get("title")]
    if knowledge:
        body += ["", "## Knowledge", ""]
        for k in knowledge:
            kind = k.get("type") or "note"
            title = k.get("title") or ""
            body.append(f"**[{kind}] {title}** — {k.get('content') or ''}".rstrip(" —"))
            if k.get("artifact"):
                body += ["", "```", str(k["artifact"]), "```"]

    return "\n".join(body).rstrip() + "\n"


def episode_file(episode: dict) -> str:
    summary = episode.get("summary") or "untitled"
    when = _day(episode.get("happened_at") or episode.get("created_at"))
    body = [
        frontmatter({
            TYPE_KEY: "episode",
            "id": episode.get("id"),
            "happened": when,
            "outcome": episode.get("outcome"),
            "valence": episode.get("emotional_valence"),
            "importance": episode.get("importance"),
            "participants": episode.get("participants") or [],
        }),
        "",
        f"# {summary}",
    ]
    if episode.get("context"):
        body += ["", episode["context"]]
    if episode.get("outcome"):
        body += ["", f"**Outcome** — {episode['outcome']}"]
    return "\n".join(body).rstrip() + "\n"


#: Must match memfmt's LINEAGE_WEIGHT. These files are read back by that
#: library, and two different discounts would render two different numbers for
#: the same record — the one failure a shared format exists to prevent.
LINEAGE_WEIGHT = 0.5


def _prior(evolution: list = None) -> tuple:
    """Beta prior for a version, taken from what its predecessor retired with.

    A bare ratio punishes the revision: a new version opens at 0/0 and reads
    worse than the one it was written to fix, so anything choosing between them
    keeps the version that already failed. Progressive delivery and CI answered
    this years ago by smoothing against a prior instead of comparing raw counts.
    """
    for entry in reversed(evolution or []):
        success, fail = entry.get("success_count"), entry.get("fail_count")
        if success is not None or fail is not None:
            return (1.0 + LINEAGE_WEIGHT * (success or 0),
                    1.0 + LINEAGE_WEIGHT * (fail or 0))
    return (1.0, 1.0)


def _reliability(success: int, fail: int, prior: tuple = (1.0, 1.0)) -> str:
    """How much to trust this version, in words.

    The word matters as much as the number: "never run" and "run and it worked"
    must not read alike, so a version standing on its lineage says `expected`
    and one with a record of its own says `reliable`.
    """
    alpha, beta = prior
    observed = success + fail
    if observed == 0 and (alpha, beta) == (1.0, 1.0):
        return "untested"
    estimate = (success + alpha) / (observed + alpha + beta)
    return f"{round(100 * estimate)}% {'reliable' if observed else 'expected'}"


def procedure_file(procedure: dict, evolution: list = None) -> str:
    """The differentiator, and the reason the export is worth having: a workflow
    with its track record and the failures that changed it."""
    name = procedure.get("name") or "unnamed"
    success = int(procedure.get("success_count") or 0)
    fail = int(procedure.get("fail_count") or 0)
    version = procedure.get("version") or 1

    body = [
        frontmatter({
            TYPE_KEY: "procedure",
            "id": procedure.get("id"),
            "version": version,
            "success_count": success,
            "fail_count": fail,
        }),
        "",
        f"# {name} (v{version} · {_reliability(success, fail, _prior(evolution))})",
    ]

    if procedure.get("trigger_condition"):
        body += ["", f"**When** — {procedure['trigger_condition']}"]

    preconditions = [p for p in ((procedure.get("metadata") or {}).get("preconditions") or []) if p]
    if preconditions:
        body += ["", "**Preconditions**", ""] + [f"- {p}" for p in preconditions]

    steps = procedure.get("steps") or []
    if steps:
        body += ["", "## Steps", ""]
        for i, step in enumerate(steps, 1):
            # steps are list[dict] with action/detail; older rows may hold plain
            # strings, and a half-written export is worse than a plain one.
            if isinstance(step, dict):
                text = step.get("action") or ""
                if step.get("detail"):
                    text = f"{text} — {step['detail']}" if text else step["detail"]
            else:
                text = str(step)
            body.append(f"{i}. {text}")

    for entry in (evolution or []):
        before, after = entry.get("version_before"), entry.get("version_after")
        note = (entry.get("diff") or {}).get("reason") or entry.get("change_type") or "revised"
        when = _day(entry.get("created_at"))
        if "## Evolution" not in body:
            body += ["", "## Evolution", ""]
        # The parenthetical carries when it happened and what the version being
        # retired had achieved by then; the second part is what a successor
        # draws its prior from, so it travels with the file.
        meta = [m for m in (when,) if m]
        prev_s, prev_f = entry.get("success_count"), entry.get("fail_count")
        if prev_s is not None or prev_f is not None:
            meta.append(f"{prev_s or 0}✓/{prev_f or 0}✗")
        stamp = f" ({', '.join(meta)})" if meta else ""
        body.append(f"- v{before} → v{after}{stamp}: {note}")

    return "\n".join(body).rstrip() + "\n"


def index_file(counts: dict, entity_stems: list) -> str:
    body = [
        frontmatter({TYPE_KEY: "index", "generated": date.today().isoformat()}),
        "",
        "# Memory",
        "",
    ]
    if not any(counts.values()):
        body += [
            "This export is empty — nothing has been remembered yet.",
            "",
            "Notes you sync, conversations you add and workflows the agent learns",
            "will appear here as files you own.",
        ]
        return "\n".join(body) + "\n"

    body += [
        f"- {counts.get('entities', 0)} entities",
        f"- {counts.get('episodes', 0)} episodes",
        f"- {counts.get('procedures', 0)} procedures",
    ]
    if entity_stems:
        body += ["", "## Entities", ""] + [f"- [[{s}]]" for s in sorted(entity_stems)]
    return "\n".join(body).rstrip() + "\n"


def build_tree(entities: list = None, episodes: list = None, procedures: list = None,
               profile: str = None, evolution_by_procedure: dict = None) -> dict:
    """The whole export as `{relative path: file text}`.

    Nothing is written here — the caller decides whether that becomes a zip, a
    directory, or files in a vault.
    """
    entities = entities or []
    episodes = episodes or []
    procedures = procedures or []
    evolution_by_procedure = evolution_by_procedure or {}

    # Names resolve to stems first, so a relation written before its target is
    # serialised still links to the right file.
    taken: set = set()
    targets = {}
    for entity in entities:
        name = entity.get("entity") or "unnamed"
        targets[name] = _unique(slugify(name), taken)

    tree = {}
    for entity in entities:
        stem = targets[entity.get("entity") or "unnamed"]
        tree[f"{ROOT}/entities/{stem}.md"] = entity_file(entity, targets)

    ep_taken: set = set()
    for episode in episodes:
        when = _day(episode.get("happened_at") or episode.get("created_at"))
        stem = _unique(
            f"{when}-{slugify(episode.get('summary') or 'episode')}" if when
            else slugify(episode.get("summary") or "episode"),
            ep_taken,
        )
        tree[f"{ROOT}/episodes/{stem}.md"] = episode_file(episode)

    proc_taken: set = set()
    for procedure in procedures:
        stem = _unique(slugify(procedure.get("name") or "procedure"), proc_taken)
        tree[f"{ROOT}/procedures/{stem}.md"] = procedure_file(
            procedure, evolution_by_procedure.get(str(procedure.get("id"))))

    if profile:
        tree[f"{ROOT}/profile.md"] = "\n".join([
            frontmatter({TYPE_KEY: "profile",
                         "generated": date.today().isoformat()}),
            "", "# Profile", "", profile.strip(),
        ]) + "\n"

    tree[f"{ROOT}/{INDEX}"] = index_file(
        {"entities": len(entities), "episodes": len(episodes), "procedures": len(procedures)},
        list(targets.values()),
    )
    return tree
