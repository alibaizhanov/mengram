"""Who is asking: the person, or the hooks working on their behalf.

The free plan allows 200 searches a month. With the Claude Code hooks installed,
auto-recall runs a search on every prompt, so a free account burns through the
month in a few days of ordinary work and then sees `quota_exceeded` in its own
context — the product switching itself off. The 2026-09-15 snapshot put a
number on it: 54 accounts had reached the search wall and 5 were paying, and
those 5 had paid on the day they signed up. The wall sold nothing; it only
disconnected people who had installed the thing.

So a search the hooks make is marked with one header and is not charged
against the search quota. It is still rate-limited, still written to the usage
log, and still costs an embedding call — the header is a courtesy for the
user's own automation, not a switch anyone is meant to flip by hand. `add`
stays counted: extraction is the expensive step and the one worth paying for.
"""

HEADER = "X-Mengram-Source"

#: The value the CLI hooks send. Anything else is an ordinary request.
HOOK = "hook"


def is_hook(headers) -> bool:
    """True when the request says it came from the user's hooks. `headers` is
    anything with a case-insensitive `get`, such as a Starlette request's."""
    try:
        return (headers.get(HEADER) or headers.get(HEADER.lower()) or "").strip().lower() == HOOK
    except Exception:
        return False


#: Header a product sets on an MCP connection to say which of its end users
#: the connection belongs to. `?user_id=` on the URL is the fallback.
END_USER_HEADER = "X-Mengram-User"


def end_user_id(raw) -> str:
    """Normalise an end-user id sent on an MCP connection: trimmed, at most
    200 chars, letters/digits and `._:@+-` only; anything else falls back to
    "default" rather than becoming a surprising sub-user."""
    raw = (raw or "").strip()
    if not raw or len(raw) > 200:
        return "default"
    if any(not (c.isalnum() or c in "._:@+-") for c in raw):
        return "default"
    return raw
