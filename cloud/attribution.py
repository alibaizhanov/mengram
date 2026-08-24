"""Where a signup came from, normalised.

Kept out of api.py so the rule is importable and testable without the web
stack, the same way the redirect policy is.

The tag arrives in a query string, which means anyone can put anything in it:
it is copied from a link we published, but also from a link somebody else
wrote. It ends up in a database column and in every report read afterwards, so
it is reduced to a small alphabet rather than trusted. The point is not
security — the insert is parameterised — but that a column used for counting
stays countable: `Reddit`, `reddit ` and `reddit` must not become three
different channels.
"""

from __future__ import annotations

import re

#: Everything outside this collapses to a single dash.
_UNSAFE = re.compile(r"[^a-z0-9._-]+")

#: Long enough for `r-localllama-weekly-thread`, short enough that a pasted
#: essay cannot become a channel name.
MAX_LENGTH = 64


def clean_source(raw) -> str | None:
    """Normalise an attribution tag, or None if nothing usable is left.

    None rather than "" on purpose: the column is NULL for every account
    created before attribution existed, and "we never knew" should not be
    confused with "they arrived with an empty tag".
    """
    if not raw:
        return None
    cleaned = _UNSAFE.sub("-", str(raw).strip().lower()).strip("-")
    return cleaned[:MAX_LENGTH].strip("-") or None
