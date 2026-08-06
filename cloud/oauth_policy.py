"""Which redirect targets may receive an OAuth authorization code.

Kept out of api.py so the rule is importable (and testable) without the web
stack. The code exchanges for a full-access API key, so an unchecked
redirect_uri turns the genuine sign-in page into a phishing endpoint: the
victim logs in on mengram.io and the code lands on the attacker's host. PKCE
does not help — a malicious client picks its own challenge.

Client registration is open, so a per-client list would be attacker-controlled;
the allowlist has to be ours.
"""

import logging
import os
import urllib.parse

logger = logging.getLogger("mengram")

_LOOPBACK = ("localhost", "127.0.0.1")


def redirect_allowlist() -> list[str]:
    """Hosts permitted to receive an authorization code. Read per call so the
    env var can be changed without a redeploy."""
    return [h.strip().lower()
            for h in os.environ.get("OAUTH_REDIRECT_ALLOWLIST", "").split(",")
            if h.strip()]


def redirect_uri_error(redirect_uri: str) -> str | None:
    """Reason this target must not receive a code, or None if it may.

    With OAUTH_REDIRECT_ALLOWLIST unset the flow behaves as before and every
    target is logged, so the live set of client callbacks can be read off the
    logs before locking it down. Set it to a comma-separated host list;
    subdomains of each entry are accepted, and loopback always is.
    """
    if not redirect_uri:
        return None

    parsed = urllib.parse.urlparse(redirect_uri)
    host = (parsed.hostname or "").lower()
    is_loopback = host in _LOOPBACK

    if parsed.scheme not in ("https", "http"):
        return "Invalid redirect_uri scheme"
    if parsed.scheme == "http" and not is_loopback:
        return "redirect_uri must use HTTPS"

    allowlist = redirect_allowlist()
    if not allowlist:
        logger.info(f"🔓 OAuth redirect target (allowlist unset): {host}")
        return None
    if is_loopback or any(host == h or host.endswith("." + h) for h in allowlist):
        return None

    logger.warning(f"🔒 OAuth redirect target rejected: {host}")
    return "redirect_uri is not allowed"
