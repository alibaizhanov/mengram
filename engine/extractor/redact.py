"""Secret redaction — canonical patterns, shared by the extractor and the CLI importer.

Coding/agent conversations routinely contain live credentials (API keys pasted
into chat, tokens in command output). They must NEVER reach the LLM or be stored
as "facts". This runs server-side on the extraction input so the model never
sees a secret it could memorize — a privacy guarantee, not a prompt suggestion.
"""
import re

# Known-prefix secrets (superset of the importer's original set).
_PREFIX_SECRETS = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,})"                # OpenAI / Anthropic
    r"|(pypi-[A-Za-z0-9_=-]{20,})"            # PyPI
    r"|(ghp_[A-Za-z0-9]{20,})|(gho_[A-Za-z0-9]{20,})|(github_pat_[A-Za-z0-9_]{20,})"
    r"|(om-[A-Za-z0-9_-]{16,})"               # Mengram
    r"|(xox[bap]-[A-Za-z0-9-]{10,})"          # Slack
    r"|(AKIA[0-9A-Z]{16})"                    # AWS access key id
    r"|(re_[A-Za-z0-9_-]{16,})"               # Resend
    r"|(AIza[A-Za-z0-9_-]{30,})"              # Google API key
    r"|(eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})"  # JWT
    r"|((?i:bearer)\s+[A-Za-z0-9._~+/=-]{16,})"
)

# Contextual secrets: "<label> is/=/: <value>" where value looks token-ish
# (>=8 chars, contains a digit or a dash). Catches generic tokens without a known
# prefix, e.g. "my Railway token is 8f3a-secret-token-999". Redacts only the value.
_CONTEXTUAL_SECRET = re.compile(
    r"(?i)\b(token|api[\s_-]?key|secret|password|passwd|credential|access[\s_-]?key)s?\b"
    r"(\s*(?:is|are|=|:)\s*)"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._~+/=-]{7,})"
)


def _redact_contextual(m: re.Match) -> str:
    return f"{m.group(1)}{m.group(2)}[REDACTED]"


def redact_secrets(text: str) -> str:
    """Replace credentials with [REDACTED]. Idempotent, never raises on str input."""
    if not text:
        return text
    text = _PREFIX_SECRETS.sub("[REDACTED]", text)
    text = _CONTEXTUAL_SECRET.sub(_redact_contextual, text)
    return text
