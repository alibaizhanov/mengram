"""Who a name can be, without a database.

When extraction does not know the speaker's name it calls them "User", and the
store forwards those facts to the account's "primary person" — the person
entity with the most facts — so a person who later introduces themselves does
not end up split in two (issue #54). That works when the person is named. When
they are not, the top person entity is whoever else got mentioned: after one
sentence about a sister, every "I play the bass" landed on "User's sister"
(E0, 2026-09-16, companion/L: recall@old 0.25 with the value retrieved and the
answer model rightly saying "unknown"). End users of a product are unnamed by
default, so this is the common case there, not a corner.

A name that is by construction *someone else* — a possessive, a relation, a
role — can never be the speaker, whatever its fact count.
"""

from __future__ import annotations

import re

#: Words that make a person entity "someone in relation to the speaker".
RELATION_WORDS = {
    "sister", "brother", "mother", "father", "mom", "dad", "mum", "parent", "parents",
    "wife", "husband", "partner", "girlfriend", "boyfriend", "spouse", "fiancé", "fiancee",
    "daughter", "son", "kid", "kids", "child", "children", "baby", "grandmother", "grandfather",
    "grandma", "grandpa", "aunt", "uncle", "cousin", "nephew", "niece", "in-law",
    "friend", "colleague", "coworker", "co-worker", "boss", "manager", "neighbour", "neighbor",
    "roommate", "flatmate", "landlord", "client", "customer", "contractor", "assistant",
    "doctor", "teacher", "coach", "therapist", "lawyer", "accountant", "reviewer", "author",
}

_POSSESSIVE = re.compile(r"(^|\s)(user|my|the user|sam)['’]s\s", re.I)


def looks_like_someone_else(name: str) -> bool:
    """True for names that cannot be the speaker: "User's sister", "my mother",
    "the neighbour", "colleague from Riga", "Dr. Chen (my doctor)"."""
    n = (name or "").strip().lower()
    if not n:
        return False
    if "'s " in n or "’s " in n or _POSSESSIVE.search(n + " "):
        return True
    words = re.findall(r"[a-z\-']+", n)
    if any(w in RELATION_WORDS for w in words):
        return True
    if n.startswith("the ") or n.startswith("my ") or n.startswith("a "):
        return True
    return False
