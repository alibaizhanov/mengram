"""When a new fact may replace an old one, and when it must not.

The contradiction pass asks a model which existing facts a new one
contradicts, then archives them. E4 (experiments/RESULTS.jsonl, 2026-09-16)
read what it had archived on a customer-support history and found the answers
to three benchmark questions in the archive:

    "has loyalty number LR-11560"       ← "has a loyalty number with the Assistant"
    "always needs one child seat"       ← "does not need a child seat for the business trip"
    "uses Visa ending 4471 for payments" ← "used Mastercard ending 9083 for a hotel"

A vaguer restatement dropped the value; a statement about one occasion
overrode a standing habit; a one-off use overrode the default. None is a
contradiction. The model is asked not to do this, and these checks make sure
of it, because the archive is where an end user's identifiers go to die.
"""
from __future__ import annotations

import re

from cloud.store._naming import RELATION_WORDS

#: An identifier, a number, a version: the part of a fact that answers a question.
VALUE = re.compile(r"\b(?:[A-Z]{1,5}-?\d{2,}[A-Z\d-]*|\d+(?:\.\d+)+|\d{2,}|#\d+)\b")

#: A fact that holds by default, not just today.
STANDING = re.compile(r"\b(always|never|usually|every (time|rental|trip|booking|week|day|morning)|"
                      r"as usual|by default|prefers?|preferred|standing|default)\b", re.I)

#: A statement about one occasion.
SCOPED = re.compile(r"\b(for (the|this|that|a|an|my|our|his|her|their) [\w' -]{0,40}?"
                    r"(trip|booking|rental|order|stay|visit|flight|hotel|meeting|event|time|occasion|weekend|"
                    r"project|session|call|day|night)|this (time|once)|(just|only) (this|that) (once|time)|"
                    r"last time|one time|once|yesterday|today|tonight|this (morning|afternoon|evening|week)|"
                    r"in (spring|summer|autumn|fall|winter)|next (week|month|trip)|on \w+day)\b", re.I)

#: A one-off past action, as opposed to a habit.
ONE_OFF = re.compile(r"^(used|took|rented|booked|paid|ordered|chose|went with|had|got|tried|picked)\b", re.I)

_RELATION = re.compile(r"\b(" + "|".join(sorted(map(re.escape, RELATION_WORDS), key=len, reverse=True)) + r")(?:['’]s)?\b", re.I)


def values(text: str) -> set[str]:
    return set(VALUE.findall(text or ""))


def why_not_supersede(old: str, new: str) -> str | None:
    """A reason the new fact must NOT archive the old one, or None if it may.

    Only the vaguer-loses-value, occasion-vs-standing, one-off-vs-default and
    someone-else cases are decided here; everything else is left to the
    model's judgement."""
    old_s, new_s = (old or "").strip(), (new or "").strip()
    old_v, new_v = values(old_s), values(new_s)
    if old_v and not new_v:
        return "new fact drops the value the old one carries"
    if _RELATION.search(new_s) and not _RELATION.search(old_s):
        return "new fact is about someone else"
    if STANDING.search(old_s) and SCOPED.search(new_s) and not STANDING.search(new_s):
        return "a statement about one occasion does not override a standing fact"
    if ONE_OFF.match(new_s) and not ONE_OFF.match(old_s) and (STANDING.search(old_s) or old_v):
        return "a one-off use does not override the default"
    return None
