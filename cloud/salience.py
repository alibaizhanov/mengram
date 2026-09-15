"""Salience gate: decide, before a fact is written, whether it is worth keeping.

E0 (experiments/RESULTS.jsonl, 2026-09-16) measured what the extractor stores on
a 90-day companion history: 37 facts, of which 21 no question ever needs. The
junk is not exotic. One intention arrives as seven facts ("wants to call the
plumber", "needs to call the plumber", "asked to be reminded to call the
plumber", …) because the only duplicate check is UNIQUE(entity_id, content);
finishing a book is stored five times; a few facts describe the conversation
instead of the person ("mentioned finishing the same book multiple times",
"planned to recommend or watch a film tonight"); and a relation gets echoed
as a fact on the other end ("Bergen: is where the User's sister moved").

The gate runs on the extractor's output, per entity, before `save_entity`. It
is deliberately lexical: no model call, no latency, and every drop has a
reason a person can read in the job result. The one thing it must never do is
merge two facts that differ in a value — "birthday is in March" and "has a
birthday in June" are two facts (the contradiction pass decides between them),
"has a dog called Pixel" and "has a cat called Pixel" are two facts. So two
facts are duplicates only when the words exclusive to either side are all
*weak*: auxiliaries, intention verbs, reporting verbs, adverbs. A noun, a
number, a month or a name on one side and not the other keeps both.

Off by default (MENGRAM_SALIENCE_GATE=1 turns it on) until E1 says what it
costs in recall.
"""
from __future__ import annotations

import os
import re

STOP = set("""a an the to of in on at for with and or but is are was were be been being am
do does did has have had it its this that these those his her their my your our
as by from into than then there here so such very""".split())

#: Words that may differ between two phrasings of the same fact. Stemmed.
WEAK = set("""want need plan intend hope wish mean aim ask remind mention say tell
talk note finish complete done read start begin try go get got make keep like
love enjoy prefer usual always often sometime still already recent just current
real also again task todo should would could will can may might must
request receive express state report confirm decide claim describe""".split())

#: A fact about the conversation rather than about the entity.
CHATTER = re.compile(
    r"\b(in (the|this) (conversation|chat|session)|multiple times|several times|"
    r"more than once|again in the|the same \w+ again|tonight|this evening|"
    r"this morning|right now|at the moment)\b", re.I)

#: A question the person asked, recorded as if it were a fact about them.
QUESTION = re.compile(r"^(asks?|asked|asking|wants to know|wonders|wondered|inquired) (for|about|how|what|whether|if|why|when|where)\b", re.I)

#: A fact that only restates a relation from the other end.
RELATION_ECHO = re.compile(
    r"^(is|was) (where|the (city|place|town|country|street|company|school) where|"
    r"(the )?user'?s |(the )?user's )|^belongs to\b", re.I)

#: The assistant's own actions this session are a log, not memory.
ASSISTANT_NAMES = {"assistant", "ai", "the assistant", "ai assistant", "claude", "chatgpt", "codex", "bot"}
ASSISTANT_LOG = re.compile(
    r"^(confirmed|checked|ran|tested|fixed|created|updated|wrote|suggested|recommended|"
    r"explained|said|told|plans? to|planned|will|helped|provided|offered|noted|"
    r"apologi[sz]ed|agreed|responded|replied|asked)\b", re.I)

_MONTHS = set("january february march april may june july august september october november december".split())


def _stem(w: str) -> str:
    for suf in ("ing", "ied", "ies", "ed", "er", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]  # planned -> plann -> plan
    return w


def content_words(fact: str) -> set[str]:
    """Words that carry the fact: lowercased, stemmed, stopwords out."""
    words = re.findall(r"[a-z0-9']+", fact.lower())
    out = set()
    for w in words:
        w = w.replace("'", "")
        if not w or w in STOP:
            continue
        out.add(_stem(w))
    return out


def _is_value(w: str) -> bool:
    """A word that changes what the fact says: not weak, or a number/month."""
    return w.isdigit() or w in _MONTHS or w not in WEAK


def same_fact(a: str, b: str) -> bool:
    """True when `a` and `b` are two phrasings of one fact: they share most
    content words and every word only one of them has is weak."""
    wa, wb = content_words(a), content_words(b)
    if not wa or not wb:
        return False
    shared = wa & wb
    if not any(_is_value(w) for w in shared):
        return False  # only weak words in common: "wants to" ≠ "wants to"
    exclusive = (wa ^ wb)
    if any(_is_value(w) for w in exclusive):
        return False
    return len(shared) / len(wa | wb) >= 0.34


def reason_to_drop(fact: str, entity: str | None = None) -> str | None:
    """Why this fact, on its own, is not memory. None when it may be."""
    text = (fact or "").strip()
    if len(re.findall(r"[a-z0-9]+", text.lower())) <= 1:
        return "one word"
    if CHATTER.search(text):
        return "about the conversation, not the person"
    if QUESTION.match(text):
        return "a question asked, not a fact"
    ent = (entity or "").strip().lower()
    if ent in ASSISTANT_NAMES and ASSISTANT_LOG.match(text):
        return "assistant's own action this session"
    if ent and ent not in ("user",) and RELATION_ECHO.match(text):
        return "restates a relation from the other end"
    return None


def gate(facts: list[str], existing: list[str] | None = None, entity: str | None = None
         ) -> tuple[list[str], list[dict]]:
    """Split `facts` into those worth writing and those not, with reasons.

    `existing` are the entity's current live facts; a new fact that only
    rephrases one of them, or rephrases an earlier fact in the same batch, is
    dropped. The first phrasing in a batch wins."""
    kept: list[str] = []
    dropped: list[dict] = []
    against = list(existing or [])
    for fact in facts or []:
        why = reason_to_drop(fact, entity)
        if why is None:
            twin = next((o for o in against if same_fact(fact, o)), None)
            if twin is not None:
                why = f"same as: {twin}"
        if why is None:
            kept.append(fact)
            against.append(fact)
        else:
            dropped.append({"fact": fact, "reason": why})
    return kept, dropped


def enabled() -> bool:
    return os.environ.get("MENGRAM_SALIENCE_GATE", "").strip().lower() in ("1", "true", "yes", "on")
