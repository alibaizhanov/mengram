"""The folder store: a memfmt tree read into memory, changed, written back.

Everything here is deliberately plain. The folder is the database; this class
is only the in-memory view of it plus the rules for changing it — the same
rules the cloud store applies, on the same pure helpers, so a workflow means
one thing whether it lives in Postgres or in `procedures/deploy.md`.

Retrieval is word overlap, the honest limit of files without a model. Past a
few hundred entries you want embeddings, and that is what the cloud is for.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import memfmt
from memfmt import Entity, Episode, Knowledge, Memory, Procedure, Relation, Step

from cloud.procedure_match import (
    apply_step_outcome, is_near_duplicate_procedure, normalize_step, procedure_similarity,
)
_WORD = re.compile(r"[a-z0-9][a-z0-9_./-]{1,}")
_STOP = frozenset({
    "the", "and", "for", "with", "from", "into", "then", "that", "this", "what",
    "when", "where", "which", "who", "how", "why", "does", "did", "was", "were",
    "are", "have", "has", "had", "you", "your", "our", "its", "his", "her", "they",
    "them", "about", "after", "before", "over", "under", "just", "also", "not",
})


def _tokens(text: str) -> set:
    return {w.strip("./-") for w in _WORD.findall((text or "").lower())
            if w not in _STOP and len(w) > 2} - {""}


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split()).rstrip(".")


def _today() -> str:
    return _dt.date.today().isoformat()


class LocalStore:
    """A memfmt folder with the cloud's rules for changing it."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.memory: Memory = memfmt.load(self.root) if self.root.is_dir() else Memory()

    # ---- persistence ------------------------------------------------------

    def save(self) -> list:
        """Write the whole tree back. Returns the paths written."""
        tree = memfmt.serialise(memfmt.canonical(self.memory), root="")
        self.root.mkdir(parents=True, exist_ok=True)
        return memfmt.write_dir(tree, self.root)

    # ---- lookups ----------------------------------------------------------

    def _entity(self, name: str) -> Entity | None:
        key = _norm(name)
        for e in self.memory.entities:
            if _norm(e.name) == key:
                return e
        return None

    def _procedure(self, name: str) -> Procedure | None:
        key = _norm(name)
        for p in self.memory.procedures:
            if _norm(p.name) == key:
                return p
        return None

    # ---- ingestion --------------------------------------------------------

    def add_extraction(self, extraction) -> dict:
        """Fold an `ExtractionResult` into the memory. Does not write; call save().

        Entities merge by name, facts dedup by normalised text, relations and
        knowledge append if new. Episodes append. Procedures go through the
        same near-duplicate rule as the cloud: created, refreshed in place if
        the existing one has never run, kept untouched if it has a record.
        """
        stats = {"entities_created": 0, "entities_updated": 0, "facts_added": 0,
                 "episodes_saved": 0, "procedures": {"created": 0, "refreshed": 0, "kept": 0}}

        for ext in extraction.entities or []:
            if not ext.name:
                continue
            entity = self._entity(ext.name)
            if entity is None:
                entity = Entity(name=ext.name.strip(), entity_type=ext.entity_type or None)
                self.memory.entities.append(entity)
                stats["entities_created"] += 1
            else:
                stats["entities_updated"] += 1
                if not entity.entity_type and ext.entity_type:
                    entity.entity_type = ext.entity_type
            known = {_norm(f) for f in entity.facts}
            for fact in ext.facts or []:
                text = fact.content if hasattr(fact, "content") else str(fact)
                text = " ".join(str(text).split())
                if text and _norm(text) not in known:
                    entity.facts.append(text)
                    known.add(_norm(text))
                    stats["facts_added"] += 1

        for rel in extraction.relations or []:
            src = self._entity(rel.from_entity) if rel.from_entity else None
            if src is None or not rel.to_entity:
                continue
            exists = any(_norm(r.target) == _norm(rel.to_entity) and _norm(r.type) == _norm(rel.relation_type)
                         for r in src.relations)
            if not exists:
                src.relations.append(Relation(type=rel.relation_type or "related to",
                                              target=rel.to_entity.strip(),
                                              detail=rel.description or None))

        for k in extraction.knowledge or []:
            owner = self._entity(k.entity) if k.entity else None
            if owner is None:
                continue
            if any(_norm(x.title) == _norm(k.title) for x in owner.knowledge):
                continue
            owner.knowledge.append(Knowledge(type=k.knowledge_type or "note", title=k.title or "",
                                             content=k.content or "", artifact=k.artifact))

        for ep in extraction.episodes or []:
            if not ep.summary:
                continue
            happened = ep.happened_at or _today()
            if any(_norm(e.summary) == _norm(ep.summary) and e.happened == happened
                   for e in self.memory.episodes):
                continue
            importance = ep.importance
            if isinstance(importance, float) and importance <= 1.0:
                importance = round(importance * 5)
            self.memory.episodes.append(Episode(
                summary=" ".join(ep.summary.split()), happened=happened,
                outcome=ep.outcome or None, valence=ep.emotional_valence or None,
                importance=int(importance) if importance is not None else None,
                participants=list(ep.participants or []), context=ep.context or None,
            ))
            stats["episodes_saved"] += 1

        for pr in extraction.procedures or []:
            if not pr.name or not pr.steps:
                continue
            action = self.save_extracted_procedure(pr.name, pr.trigger, pr.steps,
                                                   entities=getattr(pr, "entities", None))
            stats["procedures"][action] += 1

        return stats

    def save_extracted_procedure(self, name: str, trigger: str | None, steps: list,
                                 entities: list | None = None) -> str:
        """created | refreshed | kept — the cloud's write-time dedup, on files.

        `entities` (what the workflow touches) ride in the file's frontmatter
        as `entities:`; the regression gate uses them to tell which
        procedures share a surface."""
        new_steps = _to_steps(steps)
        ents = [str(e).strip() for e in (entities or []) if str(e).strip()]
        exact = self._procedure(name)
        if exact is not None:
            # The unique-name case: refresh the description, never the record.
            if exact.success_count + exact.fail_count == 0:
                exact.steps = new_steps
                exact.trigger = trigger or exact.trigger
                _merge_entities(exact, ents)
                return "refreshed"
            return "kept"

        best, best_score = None, 0.0
        for p in self.memory.procedures:
            name_sim, step_sim = procedure_similarity(name, steps, p.name, _steps_as_dicts(p.steps))
            if is_near_duplicate_procedure(name_sim, step_sim) and name_sim + step_sim > best_score:
                best, best_score = p, name_sim + step_sim
        if best is None:
            proc = Procedure(name=name.strip(), trigger=trigger or None, steps=new_steps)
            _merge_entities(proc, ents)
            self.memory.procedures.append(proc)
            return "created"
        if best.success_count + best.fail_count > 0:
            return "kept"
        best.steps = new_steps
        best.trigger = trigger or best.trigger
        _merge_entities(best, ents)
        return "refreshed"

    def existing_context(self, max_entities: int = 40) -> str:
        """What the extractor is told the memory already holds, so it reuses
        names instead of minting near-duplicates."""
        if not self.memory.entities:
            return ""
        lines = ["Known entities (reuse these names):"]
        for e in self.memory.entities[:max_entities]:
            facts = "; ".join(e.facts[:3])
            kind = f" ({e.entity_type})" if e.entity_type else ""
            lines.append(f"- {e.name}{kind}: {facts}" if facts else f"- {e.name}{kind}")
        return "\n".join(lines)

    def add(self, conversation, llm_client) -> dict:
        """Extract with the user's model and fold the result in. Writes."""
        from engine.extractor.conversation_extractor import ConversationExtractor
        if isinstance(conversation, str):
            conversation = [{"role": "user", "content": conversation}]
        extraction = ConversationExtractor(llm_client).extract(
            conversation, existing_context=self.existing_context())
        stats = self.add_extraction(extraction)
        self.save()
        return stats

    # ---- retrieval --------------------------------------------------------

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Entities, episodes and procedures that share words with the query,
        best first. `score` is overlap count; ties break on density."""
        q = _tokens(query)
        if not q:
            return []
        hits = []
        for e in self.memory.entities:
            text = " ".join([e.name, e.name] + e.facts + [k.title + " " + k.content for k in e.knowledge])
            words = _tokens(text)
            overlap = q & words
            if overlap:
                hits.append(((len(overlap), 1 / (1 + len(words))), {
                    "type": "entity", "entity": e.name, "entity_type": e.entity_type,
                    "facts": list(e.facts), "score": len(overlap)}))
        for ep in self.memory.episodes:
            words = _tokens(" ".join([ep.summary, ep.context or "", ep.outcome or ""]))
            overlap = q & words
            if overlap:
                hits.append(((len(overlap), 1 / (1 + len(words))), {
                    "type": "episode", "summary": ep.summary, "happened": ep.happened,
                    "outcome": ep.outcome, "valence": ep.valence, "score": len(overlap)}))
        for p in self.memory.procedures:
            words = _tokens(" ".join([p.name, p.trigger or ""] + [s.action + " " + (s.detail or "") for s in p.steps]))
            overlap = q & words
            if overlap:
                d = self._procedure_dict(p)
                d.update({"type": "procedure", "score": len(overlap)})
                hits.append(((len(overlap), 1 / (1 + len(words))), d))
        hits.sort(key=lambda h: h[0], reverse=True)
        return [h[1] for h in hits[:limit]]

    def recall(self, query: str, limit: int = 5) -> str:
        """The search, as a context block an agent can read."""
        hits = self.search(query, limit=limit)
        if not hits:
            return ""
        lines = ["[Mengram Memory — relevant context from this folder]"]
        for h in hits:
            if h["type"] == "entity":
                lines.append(f"\n{h['entity']}:")
                lines.extend(f"  - {f}" for f in h["facts"][:5])
            elif h["type"] == "episode":
                when = f" ({h['happened']})" if h.get("happened") else ""
                out = f" → {h['outcome']}" if h.get("outcome") else ""
                lines.append(f"\nEpisode{when}: {h['summary']}{out}")
            else:
                lines.append(f"\nWorkflow: {h['name']} (v{h['version']}, {h['reliability']})")
                for i, s in enumerate(h["steps"], 1):
                    lines.append(f"  {i}. {s['action']}" + (f" — {s['detail']}" if s.get("detail") else ""))
                if h.get("last_failure"):
                    lines.append(f"  last failure: {h['last_failure']}")
        return "\n".join(lines)

    def procedures(self, query: str | None = None, limit: int = 20) -> list[dict]:
        """Procedures in the cloud's dict shape (what the policy gate reads)."""
        if query:
            return [h for h in self.search(query, limit=limit * 3) if h["type"] == "procedure"][:limit]
        return [self._procedure_dict(p) for p in self.memory.procedures[:limit]]

    def _procedure_dict(self, p: Procedure) -> dict:
        steps = []
        for s in p.steps:
            d = {"action": s.action, "detail": s.detail}
            if s.tracked:
                d["success_count"] = s.success_count or 0
                d["fail_count"] = s.fail_count or 0
                d["reliability"] = s.reliability
            steps.append(d)
        return {
            "id": p.id, "name": p.name, "version": p.version,
            "entity_names": list(p.extra.get("entities") or []),
            "success_count": p.success_count, "fail_count": p.fail_count,
            "reliability": p.reliability, "trigger_condition": p.trigger,
            "preconditions": list(p.preconditions), "steps": steps,
            "last_failure": p.last_failure, "last_failed": p.last_failed,
            "evolution": [{"version_before": r.version_before, "version_after": r.version_after,
                           "reason": r.reason, "date": r.date,
                           "success_count": r.success_count, "fail_count": r.fail_count}
                          for r in p.evolution],
            "memory_type": "procedural",
        }

    # ---- outcomes ---------------------------------------------------------

    def procedure_feedback(self, name: str, success: bool, failed_at_step: int | None = None,
                           reason: str | None = None) -> dict:
        """Record a run: the procedure's counts and the steps that ran.

        A failure with a reason also sets `last_failure` / `last_failed`, so
        the next reader knows what to look at first. Revising the steps in
        response is `local.evolve`'s job, not this one's. Writes.
        """
        p = self._procedure(name)
        if p is None:
            return {"error": "procedure not found", "name": name}
        if success:
            p.success_count += 1
        else:
            p.fail_count += 1
            if reason:
                p.last_failure = " ".join(reason.split())
                p.last_failed = _today()
        step_dicts = apply_step_outcome(_steps_as_dicts(p.steps, with_counts=True), success, failed_at_step)
        p.steps = _to_steps(step_dicts, keep_counts=True, template=p.steps)
        self.save()
        return self._procedure_dict(p)

    # ---- overview ---------------------------------------------------------

    def profile(self, max_entities: int = 15) -> str:
        m = self.memory
        if not (m.entities or m.episodes or m.procedures):
            return ""
        lines = [f"Memory folder: {len(m.entities)} entities, {len(m.episodes)} episodes, "
                 f"{len(m.procedures)} procedures"]
        if m.profile:
            lines += ["", m.profile.strip()]
        if m.entities:
            lines.append("")
            for e in m.entities[:max_entities]:
                facts = "; ".join(e.facts[:2])
                kind = f" ({e.entity_type})" if e.entity_type else ""
                lines.append(f"- {e.name}{kind}" + (f": {facts}" if facts else ""))
        tested = [p for p in m.procedures if p.success_count + p.fail_count]
        if tested:
            lines.append("")
            lines.append("Workflows with a record:")
            for p in sorted(tested, key=lambda p: -(p.success_count + p.fail_count))[:10]:
                lines.append(f"- {p.name} v{p.version}: {p.success_count}✓/{p.fail_count}✗ {p.reliability}")
        return "\n".join(lines)

    def stats(self) -> dict:
        m = self.memory
        return {
            "entities": len(m.entities),
            "facts": sum(len(e.facts) for e in m.entities),
            "episodes": len(m.episodes),
            "procedures": len(m.procedures),
            "procedures_with_record": sum(1 for p in m.procedures if p.success_count + p.fail_count),
        }


def _merge_entities(proc: Procedure, entities: list) -> None:
    """Union into the `entities:` frontmatter list, order kept, case-insensitive."""
    if not entities:
        return
    current = list(proc.extra.get("entities") or [])
    seen = {e.lower() for e in current}
    for e in entities:
        if e.lower() not in seen:
            current.append(e)
            seen.add(e.lower())
    proc.extra["entities"] = current


# ---- step conversion ---------------------------------------------------------

def _to_steps(steps: list, keep_counts: bool = False, template: list | None = None) -> list:
    """Extractor/cloud step dicts → memfmt Steps. Counters the model emitted are
    dropped unless `keep_counts` (feedback path), matching the cloud's rule."""
    out = []
    for i, s in enumerate(steps or []):
        if isinstance(s, Step):
            out.append(s)
            continue
        if isinstance(s, dict):
            action = " ".join(str(s.get("action") or s.get("step") or s.get("description") or "").split())
            detail = s.get("detail")
            detail = " ".join(str(detail).split()) if detail else None
            step = Step(action=action, detail=detail)
            if keep_counts:
                step.success_count = s.get("success_count")
                step.fail_count = s.get("fail_count")
            if template and i < len(template) and isinstance(template[i], Step):
                step.needs, step.gives = list(template[i].needs), list(template[i].gives)
        else:
            step = Step(action=" ".join(normalize_step(s).split()))
        if step.action:
            out.append(step)
    return out


def _steps_as_dicts(steps: list, with_counts: bool = False) -> list:
    out = []
    for s in steps or []:
        if isinstance(s, Step):
            d = {"action": s.action, "detail": s.detail}
            if with_counts:
                d["success_count"], d["fail_count"] = s.success_count, s.fail_count
            out.append(d)
        elif isinstance(s, dict):
            out.append(dict(s))
        else:
            out.append({"action": str(s)})
    return out
