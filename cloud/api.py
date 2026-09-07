"""
Mengram Cloud API Server

Hosted version — PostgreSQL + pgvector backend.
Developers get API key, integrate in 3 lines:

    from cloud.client import CloudMemory
    m = CloudMemory(api_key="om-...")
    m.add(messages)
    results = m.search("database issues")
"""

import os
import sys
import json
import logging
import secrets
import datetime
import calendar
import uuid as _uuid
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("mengram")

from fastapi import FastAPI, HTTPException, Depends, Header, Form, Query, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, Response
from pydantic import BaseModel

from cloud.store import CloudStore, _normalize_fact
from cloud.attribution import clean_source
from cloud.oauth_policy import redirect_uri_error as _redirect_uri_error
from cloud.sub_user import SubUserScoped, resolve_sub_user as _resolve_sub_user
from cloud.auth import AuthContext
from cloud.billing import billing_router, _paddle_request, _sign_checkout_token, PADDLE_API_KEY
from cloud.plans import PLAN_QUOTAS
from cloud.site import build_site_router


FILE_SIZE_LIMITS = {
    "free":     10 * 1024 * 1024,   # 10 MB
    "starter":  10 * 1024 * 1024,   # 10 MB
    "pro":      50 * 1024 * 1024,   # 50 MB
    "growth":   100 * 1024 * 1024,  # 100 MB
    "business":   100 * 1024 * 1024,  # 100 MB
    "selfhosted": 500 * 1024 * 1024,  # 500 MB
}
ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md"}
VISION_MODEL = os.environ.get("VISION_MODEL", "gpt-5.4")

# ---- Version (single source of truth from pyproject.toml) ----
try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("mengram-ai")
except Exception:
    try:
        import re as _vre
        _pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        __version__ = _vre.search(r'^version = "([^"]+)"', _pyproject, _vre.M).group(1)
    except Exception:
        __version__ = "unknown"

# ---- Config ----

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://localhost:5432/mengram"
)
REDIS_URL = os.environ.get("REDIS_PUBLIC_URL") or os.environ.get("REDIS_URL")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Mengram <onboarding@resend.dev>")
BASE_URL = os.environ.get("BASE_URL", "https://mengram.io").rstrip("/")
DISABLE_EMAIL_VERIFICATION = os.environ.get("DISABLE_EMAIL_VERIFICATION", "").lower() in ("true", "1", "yes")
DEMO_USER_ID = os.environ.get("DEMO_USER_ID", "")

# ---- Models ----

class Message(BaseModel):
    role: str
    content: str

class AddRequest(SubUserScoped):
    messages: list[Message]
    agent_id: str | None = None
    run_id: str | None = None
    app_id: str | None = None
    source: str | None = None              # Provenance: "discord", "slack", "email", "api", etc.
    metadata: dict | None = None           # Arbitrary provenance metadata
    expiration_date: str | None = None
    dry_run: bool = False
    prompt_version: str | None = None  # Override extraction prompt version (only works with dry_run)
    agent_mode: bool = False           # True = extract from all speakers (agent actions + user), False = user-only (default)

class AddTextRequest(SubUserScoped):
    text: str
    agent_id: str | None = None
    run_id: str | None = None
    app_id: str | None = None
    source: str | None = None
    metadata: dict | None = None
    expiration_date: str | None = None

class SearchRequest(SubUserScoped):
    query: str
    agent_id: str | None = None
    run_id: str | None = None
    app_id: str | None = None
    limit: int = 5
    graph_depth: int = 2  # 0=no graph, 1=1-hop, 2=2-hop (default)
    threshold: float | None = None  # min cosine 0..1; None = server defaults
    filters: dict | None = None  # metadata filters, e.g. {"agent_id": "support-bot"}

class AskRequest(SubUserScoped):
    """RAG-style ask: synthesize an answer from memory with citations.
    Premium feature (Pro+) — uses Cohere Chat API on top of vector search."""
    query: str
    max_facts: int = 15       # how many top facts to feed Cohere as documents

class FeedbackRequest(BaseModel):
    context: str | None = None         # What went wrong (triggers evolution on failure)
    failed_at_step: int | None = None  # Which step failed

import re
import ipaddress
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Curated list of the most common disposable / throwaway email providers.
# Used to block bot signups from tempmail-style services. Kept conservative
# to avoid false positives — real users can always reply to support if blocked.
_DISPOSABLE_EMAIL_DOMAINS = frozenset({
    "10minutemail.com", "10minutemail.net", "20minutemail.com",
    "dispostable.com", "emailondeck.com", "fakeinbox.com",
    "getairmail.com", "getnada.com", "guerrillamail.com", "guerrillamail.biz",
    "guerrillamail.info", "guerrillamail.net", "guerrillamail.org",
    "guerrillamailblock.com", "inboxbear.com", "inboxkitten.com",
    "mailcatch.com", "maildrop.cc", "mailforspam.com", "mailinator.com",
    "mailinator.net", "mailnesia.com", "mailtothis.com",
    "mintemail.com", "minuteinbox.com", "mohmal.com", "mytemp.email",
    "mytrashmail.com", "nowmymail.com", "sharklasers.com",
    "spam4.me", "spambox.us", "tempail.com", "temp-mail.org",
    "tempmail.com", "tempmail.net", "tempmailo.com", "tempinbox.com",
    "tempmailaddress.com", "throwaway.email", "throwawayemailaddresses.com",
    "trashmail.com", "trashmail.net", "trashmail.io", "trashmail.de",
    "yopmail.com", "yopmail.net", "yopmail.fr",
    # Added after 2026-04 audit (see /tmp logs): domains used by abuse accounts
    "erine.email", "edny.net", "byom.de", "dropmail.me", "emlhub.com",
    "emlpro.com", "emltmp.com", "mailpoof.com", "tempmail.plus",
    "mail-temp.com", "mail-temporaire.fr", "luxusmail.org", "anonaddy.me",
    "33mail.com", "moakt.com", "harakirimail.com", "tmail.ws",
})

def _detect_query_language(text: str) -> str:
    """Hybrid language detection for Memory Health bucketing.

    Non-Latin scripts: deterministic via Unicode ranges (100% accurate
    for the script — note "ru" buckets all Cyrillic, "zh" buckets pure
    kanji Japanese alongside Chinese).

    Latin-only text: defers to langdetect for Spanish / French / German /
    Italian / Portuguese disambiguation. langdetect is unreliable on
    very short queries (<20 chars), so we only trust it above that
    threshold; otherwise default to 'en'.

    Returns ISO 639-1-ish code (en, ru, zh, ja, ko, ar, he, th, es,
    fr, de, it, pt, etc)."""
    if not text or len(text.strip()) < 2:
        return "en"
    sample = text[:500]

    # Definitive Japanese: hiragana/katakana never appear in Chinese.
    # Check whole sample so kanji-heavy Japanese isn't tagged "zh"
    # on the first kanji.
    for c in sample:
        if '぀' <= c <= 'ヿ':  # Hiragana + Katakana
            return "ja"

    # Other non-Latin scripts: first-script-wins
    for c in sample:
        if 'Ѐ' <= c <= 'ӿ':  # Cyrillic
            return "ru"
        if '一' <= c <= '鿿':  # CJK ideographs (Chinese, or pure kanji)
            return "zh"
        if '가' <= c <= '힯':  # Hangul (Korean)
            return "ko"
        if '؀' <= c <= 'ۿ':  # Arabic
            return "ar"
        if '֐' <= c <= '׿':  # Hebrew
            return "he"
        if '฀' <= c <= '๿':  # Thai
            return "th"

    # Latin-only path. Quick win first: ñ Ñ ¿ ¡ are uniquely Spanish
    # (not in French/Italian/German/Portuguese), so a single one is
    # enough to bucket as "es" — beats langdetect's Spanish/Portuguese
    # confusion on short text.
    if any(c in 'ñÑ¿¡' for c in sample):
        return "es"

    # Otherwise defer to langdetect for SP/FR/DE/IT/PT/etc disambiguation.
    # Skip if too short — langdetect is unreliable on <20 chars.
    if len(sample.strip()) < 20:
        return "en"
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0  # deterministic
        return detect(sample)
    except Exception:
        return "en"


def _is_disposable_email(email: str) -> bool:
    """Check whether the email uses a known disposable provider."""
    try:
        domain = email.split("@", 1)[1].lower().strip()
    except IndexError:
        return False
    return domain in _DISPOSABLE_EMAIL_DOMAINS

_SURROGATE_RE = re.compile(r'[\ud800-\udfff]')

def _sanitize_text(text: str) -> str:
    """Strip characters that break UTF-8 encoding or PostgreSQL storage.

    Only removes genuinely invalid characters — lone surrogates (U+D800-U+DFFF)
    and NUL bytes. All real text (emoji, CJK, Arabic, etc.) passes through intact.
    """
    text = _SURROGATE_RE.sub('', text)
    text = text.replace('\x00', '')
    return text

def _looks_like_bot_email(email: str) -> bool:
    """Heuristic detection of bot/throwaway email patterns.

    Catches gibberish like 'bsute5875hfhgdgf7489gd86@gmail.com' without
    false-positiving on legitimate users. Intentionally conservative —
    returns True only for clearly non-human patterns.
    """
    try:
        local, domain = email.split("@", 1)
    except ValueError:
        return False
    local = local.lower()

    # Pattern 1: Very long gibberish local-part (16+ chars, mixed letters+digits,
    # no vowels clustered together — suggests random generator output).
    if len(local) >= 16:
        digits = sum(c.isdigit() for c in local)
        letters = sum(c.isalpha() for c in local)
        vowels = sum(c in "aeiouy" for c in local)
        # Mostly alphanumeric mash with < 15% vowels = likely random-generated
        if digits >= 4 and letters >= 8 and vowels / max(1, letters) < 0.15:
            return True

    # Pattern 2: Extremely long digit runs (12+ consecutive digits) — bots
    # often use timestamps or fake phone numbers as prefixes.
    import re as _re
    if _re.search(r"\d{12,}", local):
        return True

    # Pattern 3: Repeating digit spam (5+ same digit in a row) — e.g. '33333',
    # '000000'. Very rare in real emails, common in lazy bot generators.
    if _re.search(r"(\d)\1{4,}", local):
        return True

    # Pattern 4: Long prefix with digits dominating letters (suggests ID mash-up
    # like 'queenking03705336564' — 9 letters + 14 digits).
    if len(local) >= 15:
        digits = sum(c.isdigit() for c in local)
        letters = sum(c.isalpha() for c in local)
        if letters >= 4 and digits > letters * 1.3:
            return True

    return False

def _is_private_url(url: str) -> bool:
    """Check if URL points to private/internal network (SSRF protection)."""
    import urllib.parse
    import socket
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    hostname = parsed.hostname or ""
    if not hostname:
        return True
    # Block well-known internal hostnames
    if hostname in ("localhost", "0.0.0.0", "metadata.google.internal") or hostname.endswith(".internal") or hostname.endswith(".local"):
        return True
    # Try to resolve hostname and check IP
    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in resolved:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except (socket.gaierror, ValueError):
        pass  # Can't resolve — allow (will fail at send time)
    return False


def _require_full_uuid(value: str, field_name: str = "id") -> None:
    """Raise 400 if value is not a full UUID. Guards against clients passing 8-char prefix IDs."""
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a full UUID")


class SignupRequest(BaseModel):
    email: str
    website: str = ""  # Honeypot — hidden form field, real users leave empty, bots fill
    source: str = ""   # Where they came from, if the landing page knew

    @property
    def validated_email(self) -> str:
        e = self.email.strip().lower()
        if not e or len(e) > 254 or not _EMAIL_RE.match(e):
            raise ValueError("Invalid email address")
        return e

class SignupResponse(BaseModel):
    api_key: str
    message: str

class VerifyRequest(BaseModel):
    email: str
    code: str
    source: str = ""   # Carried through the two-step flow from the first request

    @property
    def validated_email(self) -> str:
        e = self.email.strip().lower()
        if not e or len(e) > 254 or not _EMAIL_RE.match(e):
            raise ValueError("Invalid email address")
        return e

class ResetKeyRequest(BaseModel):
    email: str

    @property
    def validated_email(self) -> str:
        e = self.email.strip().lower()
        if not e or len(e) > 254 or not _EMAIL_RE.match(e):
            raise ValueError("Invalid email address")
        return e


# ---- App ----

def create_cloud_api() -> FastAPI:
    app = FastAPI(
        title="Mengram API",
        description="""
## Human-Like Memory for AI — Semantic + Episodic + Procedural

The only AI memory API with 3 memory types. Your AI remembers facts, events, and learned workflows.

### 3 Memory Types
- **Semantic** — facts, preferences, skills (entities, relations, knowledge graph)
- **Episodic** — events, decisions, experiences (what happened, when, outcome)
- **Procedural** — workflows, processes, habits (learned step-by-step procedures)

### Key Features
- **Cognitive Profile** — one API call generates a system prompt from all memory types
- **Unified Search** — search across all 3 types simultaneously
- **Procedure Feedback** — AI learns which workflows succeed
- **Memory Agents** — autonomous cleanup, pattern detection, weekly digests
- **Team Sharing** — shared memory across team members
- **LangChain** — drop-in replacement for ConversationBufferMemory
- **CrewAI** — 5 tools with procedural learning (agents learn optimal workflows)
- **OpenClaw** — plugin with auto-recall/capture hooks, 12 tools, and Graph RAG across all channels

### Authentication
All endpoints require `Authorization: Bearer YOUR_API_KEY` header.

### Quick Start
```python
from mengram import Mengram
m = Mengram(api_key="om-...")
m.add([{"role": "user", "content": "I use Python and Railway"}])
results = m.search_all("deployment")  # semantic + episodic + procedural
profile = m.get_profile()             # instant system prompt
```
        """,
        version=__version__,
        docs_url="/swagger",
        redoc_url="/redoc",
        openapi_tags=[
            {"name": "Memory", "description": "Store and retrieve semantic memories"},
            {"name": "Episodic Memory", "description": "Events, decisions, experiences — what happened"},
            {"name": "Procedural Memory", "description": "Workflows, processes — how to do things"},
            {"name": "Search", "description": "Semantic and unified search across all memory types"},
            {"name": "Agents", "description": "Autonomous memory agents — Curator, Connector, Digest"},
            {"name": "Teams", "description": "Shared team memory with invite codes"},
            {"name": "Webhooks", "description": "HTTP notifications on memory events"},
            {"name": "Insights", "description": "AI-generated reflections and patterns"},
            {"name": "System", "description": "Health, stats, and account management"},
        ],
    )

    from starlette.middleware.base import BaseHTTPMiddleware

    class RateLimitHeaderMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if hasattr(request.state, 'rate_limit'):
                response.headers["X-RateLimit-Limit"] = str(request.state.rate_limit)
                response.headers["X-RateLimit-Remaining"] = str(request.state.rate_remaining)
                response.headers["X-RateLimit-Reset"] = "60"
            if hasattr(request.state, 'quota_info'):
                qi = request.state.quota_info
                for action in ("add", "search"):
                    if action in qi:
                        prefix = f"X-Quota-{action.capitalize()}"
                        response.headers[f"{prefix}-Used"] = str(qi[action]["used"])
                        response.headers[f"{prefix}-Limit"] = str(qi[action]["limit"])
            return response

    app.add_middleware(RateLimitHeaderMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset",
            "X-Quota-Add-Used", "X-Quota-Add-Limit",
            "X-Quota-Search-Used", "X-Quota-Search-Limit",
        ],
    )

    # Connection budget: Supabase session-mode pooler caps clients at 15.
    # api service (2 gunicorn workers × pool_max) + cron worker instance +
    # deploy overlap (old and new instances alive simultaneously) must all
    # fit under that cap — pool_max=10 caused "Worker failed to boot"
    # (EMAXCONNSESSION) on deploys (observed 2026-07-21). Budget with 1/4:
    # api 2×4=8, worker ≤4, overlap +2 → 14 < 15. History: pool_max=2
    # deadlocked under 3+ concurrent requests; 4 keeps 4× that headroom.
    _POOL_MIN = int(os.environ.get("POOL_MIN", "1"))
    _POOL_MAX = int(os.environ.get("POOL_MAX", "4"))
    store = CloudStore(DATABASE_URL, pool_min=_POOL_MIN, pool_max=_POOL_MAX, redis_url=REDIS_URL)

    # LLM client for extraction (shared)
    _llm_client = None
    _extractor = None

    def get_llm():
        nonlocal _llm_client, _extractor
        if _llm_client is None:
            from engine.extractor.llm_client import create_llm_client
            llm_model = os.environ.get("LLM_MODEL", "")
            llm_config = {
                "provider": os.environ.get("LLM_PROVIDER", "anthropic"),
                "anthropic": {"api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
                              **({"model": llm_model} if llm_model else {})},
                "openai": {"api_key": os.environ.get("OPENAI_API_KEY", ""),
                            **({"model": llm_model} if llm_model else {})},
            }
            _llm_client = create_llm_client(llm_config)
            from engine.extractor.conversation_extractor import ConversationExtractor
            _extractor = ConversationExtractor(_llm_client)
        return _extractor

    # Embedder (shared — API-based, no PyTorch)
    _embedder = None

    def get_embedder():
        nonlocal _embedder
        if _embedder is None:
            from cloud.embedder import create_embedder
            _embedder = create_embedder()
        return _embedder

    # ---- Re-ranking (Cohere Rerank → LLM fallback) ----
    _cohere_client = None
    _openai_rerank_client = None

    def _summarize_for_embedding(text: str, max_chars: int = 1500) -> str:
        """Summarize long text for embedding. Preserves key facts for search."""
        if len(text) <= max_chars:
            return text
        try:
            openai_key = os.environ.get("OPENAI_API_KEY", "")
            if not openai_key:
                return text[:max_chars]
            import openai
            client = openai.OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": f"Summarize this into a dense, fact-rich paragraph under {max_chars} characters. Keep all key facts, names, technologies, and outcomes:\n\n{text[:30000]}"}],
                max_completion_tokens=500,
            )
            summary = (resp.choices[0].message.content or "").strip()
            return summary if summary else text[:max_chars]
        except Exception as e:
            logger.debug(f"Summarize for embedding failed, truncating: {e}")
            return text[:max_chars]

    def rerank_results(query: str, results: list[dict], plan: str = "business") -> list[dict]:
        """Re-rank search results based on subscription plan.
        Free/Starter: no reranking.  Pro/Growth/Business: Cohere Rerank → LLM fallback."""
        if not results or len(results) <= 1:
            return results

        # Free/Starter: no reranking — return raw vector results
        if plan in ("free", "starter"):
            return results

        # Try Cohere Rerank first — fact-level (cross-encoder, more precise)
        cohere_key = os.environ.get("COHERE_API_KEY", "") if plan in ("pro", "growth", "business", "selfhosted") else ""
        if cohere_key:
            try:
                nonlocal _cohere_client
                if _cohere_client is None:
                    import cohere
                    _cohere_client = cohere.ClientV2(api_key=cohere_key)
                co = _cohere_client

                # Build one document per fact (not per entity)
                fact_docs = []  # [(entity_idx, fact_idx, doc_text)]
                for eidx, r in enumerate(results):
                    name = r.get("entity", "")
                    for fidx, fact in enumerate(r.get("facts", [])):
                        fact_docs.append((eidx, fidx, f"{name}: {fact}"))

                if not fact_docs:
                    return results

                documents = [fd[2] for fd in fact_docs]
                # rerank-v4.0-pro: 32k context, native multilingual (pairs with our
                # Cohere multilingual embed). rerank-v4.0-fast was English-leaning.
                # Override via env if rollback needed.
                rerank_model = os.environ.get("COHERE_RERANK_MODEL", "rerank-v4.0-pro")
                resp = co.rerank(
                    model=rerank_model,
                    query=query,
                    documents=documents,
                    top_n=min(len(documents), 50),
                )

                # Group relevant facts back by entity
                entity_facts = {}  # entity_idx → [(fact_text, score)]
                for item in resp.results:
                    if item.relevance_score >= 0.15:
                        eidx, fidx, _ = fact_docs[item.index]
                        fact_text = results[eidx]["facts"][fidx]
                        if eidx not in entity_facts:
                            entity_facts[eidx] = []
                        entity_facts[eidx].append((fact_text, item.relevance_score))

                # Rebuild results: only entities with relevant facts, facts reordered.
                # Sort entities by their BEST fact relevance (not by original vector order),
                # so the entity with the most query-relevant fact comes first.
                reranked = []
                ordered_eidx = sorted(
                    entity_facts.keys(),
                    key=lambda e: max(s for _, s in entity_facts[e]),
                    reverse=True,
                )
                for eidx in ordered_eidx:
                    r = dict(results[eidx])
                    scored_facts = sorted(entity_facts[eidx], key=lambda x: x[1], reverse=True)
                    r["facts"] = [f[0] for f in scored_facts[:7]]
                    # Surface rerank confidence so downstream (and clients) see real relevance,
                    # not the tiny RRF score.
                    r["score"] = float(scored_facts[0][1])
                    reranked.append(r)
                return reranked if reranked else results

            except Exception as e:
                logger.warning(f"⚠️ Cohere rerank failed, falling back: {e}")

        # Fallback: LLM rerank
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            return results

        try:
            nonlocal _openai_rerank_client
            if _openai_rerank_client is None:
                import openai
                _openai_rerank_client = openai.OpenAI(api_key=openai_key)
            client = _openai_rerank_client

            candidates = []
            for i, r in enumerate(results):
                facts_str = "; ".join(_normalize_fact(f) for f in r.get("facts", [])[:5])
                rels_str = "; ".join(
                    f"{rel.get('type', '')} {rel.get('target', '')}"
                    for rel in r.get("relations", [])[:3]
                )
                info = f"[{i}] {r['entity']} ({r['type']}): {facts_str}"
                if rels_str:
                    info += f" | relations: {rels_str}"
                candidates.append(info)

            prompt = f"""Given the user's query, select ONLY the entities that are directly relevant.

Query: "{query}"

Candidates:
{chr(10).join(candidates)}

Return ONLY a JSON array of indices of relevant entities, e.g. [0, 2, 4].
If none are relevant, return [].
Be strict — only include entities that directly answer or relate to the query."""

            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=100,
                temperature=0,
            )

            text = (resp.choices[0].message.content or "").strip()
            if not text:
                return results

            import json as json_mod
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()
            indices = json_mod.loads(text)

            if isinstance(indices, list) and all(isinstance(i, int) for i in indices):
                filtered = [results[i] for i in indices if 0 <= i < len(results)]
                if filtered:
                    return filtered

            return results

        except Exception as e:
            logger.debug(f"LLM rerank skipped, using raw results: {e}")
            return results

    # ---- Rate Limiting (Redis-shared or in-memory fallback) ----
    _rate_limits = {}  # fallback: user_id -> {"count": N, "window_start": time}
    _rate_lock = __import__('threading').Lock()
    RATE_WINDOW = 60   # seconds

    def _check_rate_limit(user_id: str, limit: int = 120) -> bool:
        """Returns True if allowed, False if rate limited.
        Uses Redis INCR for cross-worker consistency when available."""
        # Try Redis first (shared across workers)
        redis_client = getattr(store.cache, '_redis', None) if store else None
        if redis_client:
            try:
                key = f"rl:{user_id}"
                count = redis_client.incr(key)
                if count == 1:
                    redis_client.expire(key, RATE_WINDOW)
                return count <= limit
            except Exception:
                pass  # fall through to in-memory

        # In-memory fallback (per-worker)
        import time as _time
        now = _time.time()
        with _rate_lock:
            entry = _rate_limits.get(user_id)
            if not entry or now - entry["window_start"] >= RATE_WINDOW:
                _rate_limits[user_id] = {"count": 1, "window_start": now}
                return True
            if entry["count"] >= limit:
                return False
            entry["count"] += 1
            return True

    # ---- Playground Rate Limiting (hourly, IP-based) ----
    _playground_rate_limits = {}
    PLAYGROUND_RATE_WINDOW = 3600  # 1 hour

    def _check_playground_rate_limit(client_ip: str, limit: int = 30, prefix: str = "playground") -> bool:
        """Hourly rate limit for playground. Returns True if allowed."""
        redis_client = getattr(store.cache, '_redis', None) if store else None
        if redis_client:
            try:
                key = f"rl:{prefix}:{client_ip}"
                count = redis_client.incr(key)
                if count == 1:
                    redis_client.expire(key, PLAYGROUND_RATE_WINDOW)
                return count <= limit
            except Exception:
                pass
        import time as _time
        now = _time.time()
        rate_key = f"{prefix}:{client_ip}"
        with _rate_lock:
            entry = _playground_rate_limits.get(rate_key)
            if not entry or now - entry["window_start"] >= PLAYGROUND_RATE_WINDOW:
                _playground_rate_limits[rate_key] = {"count": 1, "window_start": now}
                return True
            if entry["count"] >= limit:
                return False
            entry["count"] += 1
            return True

    # ---- Quota checking ----

    def _quota_cache_key(user_id: str, action: str) -> str:
        """Redis key for quota counter: qc:{user_id}:{action}:{YYYY-MM}"""
        today = datetime.date.today()
        return f"qc:{user_id}:{action}:{today.year}-{today.month:02d}"

    def _quota_month_end_ttl() -> int:
        """Seconds until end of current month (for EXPIREAT)."""
        today = datetime.date.today()
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        return (days_in_month - today.day + 1) * 86400

    def _quality_label(top_score: float) -> str:
        """Scale-aware retrieval quality. query_score mixes two scales
        (rerank/cosine 0-1 vs raw RRF topping out ~0.05), so raw thresholds
        misread healthy RRF results as failures — use this label instead."""
        if top_score >= 0.3:
            return "strong"
        if top_score >= 0.02:
            return "weak"
        return "no_match"

    def use_quota(ctx: AuthContext, action: str, count: int = 1):
        """Atomically check quota AND increment usage in one operation.
        Uses Redis counter cache for fast-reject before hitting PostgreSQL."""
        quota_map = {
            "add": "adds", "search": "searches", "agent": "agents",
            "reflect": "reflects", "dedup": "dedups", "reindex": "reindexes",
            "rules": "rules",
        }
        quota_key = quota_map.get(action)
        if not quota_key:
            return
        plan_quotas = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
        max_allowed = plan_quotas.get(quota_key, 0)
        if max_allowed == -1:
            return  # unlimited

        # Step 1: Fast-reject via Redis counter cache (0 DB hits)
        redis_client = getattr(store.cache, '_redis', None)
        cache_key = _quota_cache_key(ctx.user_id, action)
        try:
            if redis_client:
                cached = redis_client.get(cache_key)
                if cached is not None and int(cached) >= max_allowed:
                    _raise_quota_error(action, max_allowed, int(cached), ctx.plan, ctx.user_id)
        except Exception:
            pass  # Redis down → fall through to DB

        # Step 2: Atomic check-and-increment in PostgreSQL
        new_count = 0
        try:
            new_count = store.check_and_increment(ctx.user_id, action, max_allowed, count)
        except ValueError as e:
            parts = str(e).split(":")
            if parts[0] == "quota_exceeded":
                current = int(parts[2]) if len(parts) > 2 else max_allowed
                limit = int(parts[3]) if len(parts) > 3 else max_allowed
                # Update Redis counter to actual DB value (self-correction)
                try:
                    if redis_client:
                        redis_client.set(cache_key, str(current), ex=_quota_month_end_ttl())
                except Exception:
                    pass
                _raise_quota_error(action, limit, current, ctx.plan, ctx.user_id)
            raise

        # Step 3: Success — update Redis counter from DB value
        try:
            if redis_client:
                db_count = store.get_usage_count(ctx.user_id, action)
                redis_client.set(cache_key, str(db_count), ex=_quota_month_end_ttl())
                if db_count > new_count:
                    new_count = db_count
        except Exception:
            pass  # Redis down → counter will be set on next request

        # Step 4: 80% quota warning email (one-time per month, deduped via drip_emails)
        if action in ("add", "search") and max_allowed > 0:
            threshold = int(max_allowed * 0.8)
            if new_count >= threshold:
                # Just crossed 80% — send warning
                try:
                    _email = store.get_user_email(ctx.user_id)
                    if _email:
                        import threading
                        threading.Thread(
                            target=_send_quota_warning_email,
                            args=(ctx.user_id, _email, ctx.plan, action, new_count, max_allowed),
                            daemon=True,
                        ).start()
                except Exception:
                    pass

    # Log suppression for repeated quota blocks: {user_action: (last_log_time, count)}
    _quota_log_tracker: dict = {}

    def _raise_quota_error(action, max_allowed, current, plan, user_id=None):
        if user_id:
            import time as _time
            tracker_key = f"{user_id[:8]}:{action}"
            now = _time.time()
            entry = _quota_log_tracker.get(tracker_key)
            if entry is None or (now - entry[0]) >= 1800:
                # First block or 30 min since last log — log with suppressed count
                suppressed = entry[1] if entry else 0
                suffix = f" ({suppressed} blocked requests suppressed)" if suppressed > 0 else ""
                logger.warning(f"🚫 QUOTA {action} | user={user_id[:8]} | {current}/{max_allowed} | plan={plan}{suffix}")
                _quota_log_tracker[tracker_key] = (now, 0)
            else:
                # Suppress log, just count
                _quota_log_tracker[tracker_key] = (entry[0], entry[1] + 1)
        # Send one-time upgrade email (non-blocking, deduped per month)
        if user_id and action in ("add", "search"):
            try:
                _email = store.get_user_email(user_id)
                if _email:
                    import threading
                    threading.Thread(
                        target=_send_quota_email,
                        args=(user_id, _email, plan, action, max_allowed),
                        daemon=True,
                    ).start()
            except Exception:
                pass
        retry_after = _quota_month_end_ttl()
        # Build direct one-click checkout URL (same as quota email)
        next_plan_key = {"free": "starter", "starter": "pro", "pro": "growth", "growth": "business"}.get(plan, "starter")
        upgrade_url = f"{BASE_URL}/#pricing"
        if user_id:
            token = _sign_checkout_token(user_id, next_plan_key)
            if token:
                upgrade_url = f"{BASE_URL}/checkout?token={token}"
        next_plan = NEXT_PLAN_INFO.get(plan)
        upgrade_msg = f"Upgrade to {next_plan['name']} ({next_plan['price']})" if next_plan else "Upgrade your plan"
        # Value mirror: show intelligence summary so clients can display accumulated value
        intelligence = None
        if user_id:
            try:
                intelligence = store.get_value_mirror(user_id)
            except Exception:
                pass
        raise HTTPException(
            status_code=402,
            detail={
                "error": "quota_exceeded",
                "action": action,
                "limit": max_allowed,
                "used": current,
                "plan": plan,
                "upgrade_url": upgrade_url,
                "message": f"Monthly {action} limit reached ({max_allowed}). {upgrade_msg} at {upgrade_url}",
                "retry_after": retry_after,
                "intelligence": intelligence,
            },
            headers={
                "Retry-After": str(retry_after),
            },
        )

    # ---- Quota limit email notification ----

    NEXT_PLAN_INFO = {
        "free": {
            "name": "Starter",
            "price": "$5/mo",
            "adds": "100",
            "searches": "500",
            "features": "higher rate limits, webhooks, and team collaboration",
        },
        "starter": {
            "name": "Pro",
            "price": "$19/mo",
            "adds": "1,000",
            "searches": "10,000",
            "features": "LLM-powered reranking, procedure evolution, and smart triggers",
        },
        "pro": {
            "name": "Growth",
            "price": "$59/mo",
            "adds": "3,000",
            "searches": "20,000",
            "features": "unlimited agents, 200 req/min, and 25 webhooks",
        },
        "growth": {
            "name": "Business",
            "price": "$99/mo",
            "adds": "8,000",
            "searches": "30,000",
            "features": "Cohere cross-encoder reranking and unlimited teams",
        },
    }

    def _send_quota_email(user_id: str, email: str, plan: str, action: str, max_allowed: int):
        """Send one-time email when user hits quota. Shows next plan up. Deduped monthly."""
        now = datetime.datetime.now(datetime.timezone.utc)
        drip_type = f"quota_{action}_{now.strftime('%Y-%m')}"

        # Re-verify plan from DB (bypass cache) — caller's `plan` may be stale if user
        # upgraded between auth() and the quota trigger. Avoids sending free-tier
        # quota emails to paying customers (saw with Ben Hartley on April 2: got
        # quota_search at 1% of Growth limit because ctx.plan was cached as "free").
        try:
            store.cache.invalidate(f"sub:{user_id}")
            fresh_sub = store.get_subscription(user_id)
            fresh_plan = fresh_sub.get("plan", "free") if fresh_sub else "free"
            if fresh_plan != plan and fresh_plan not in ("free",):
                logger.warning(
                    f"🛑 Suppressed {drip_type} for {user_id[:8]} — caller plan={plan}, fresh plan={fresh_plan}"
                )
                return
        except Exception as e:
            logger.warning(f"Quota email plan re-check failed for {user_id[:8]}: {e}")

        if not store.try_record_drip(email, drip_type, user_id):
            return  # already sent this month

        resend_key = os.environ.get("RESEND_API_KEY")
        if not resend_key:
            return

        next_plan = NEXT_PLAN_INFO.get(plan)
        action_label = "memory adds" if action == "add" else "searches"

        if next_plan:
            subject = f"You've reached your monthly {action_label} limit"
            next_limit = next_plan["adds"] if action == "add" else next_plan["searches"]
            next_plan_key = {"free": "starter", "starter": "pro", "pro": "growth", "growth": "business"}.get(plan, "starter")
            checkout_token = _sign_checkout_token(user_id, next_plan_key)
            checkout_url = f"{BASE_URL}/checkout?token={checkout_token}"
            body_html = f"""
            <p style="font-size:15px;color:#c8c8d8;line-height:1.6">
                You've used all {max_allowed:,} {action_label} on your {plan} plan this month.
            </p>
            <p style="font-size:15px;color:#c8c8d8;line-height:1.6">
                Upgrade to <strong style="color:#a78bfa">{next_plan['name']}</strong> ({next_plan['price']}) for
                {next_limit} {action_label}/month, {next_plan['features']}.
            </p>
            <div style="text-align:center;margin:28px 0">
                <a href="{checkout_url}"
                   style="background:#7c3aed;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-size:15px;font-weight:600">
                    Upgrade to {next_plan['name']}
                </a>
            </div>
            <p style="font-size:13px;color:#55556a">Your limits reset at the start of each month.</p>"""
        else:
            # Business plan → Enterprise (reply-based)
            subject = f"You've hit your Business plan {action_label} limit"
            body_html = f"""
            <p style="font-size:15px;color:#c8c8d8;line-height:1.6">
                You've reached your Business {action_label} limit ({max_allowed:,}/month).
            </p>
            <p style="font-size:15px;color:#c8c8d8;line-height:1.6">
                Let's set up a custom Enterprise plan for your usage — just reply to this email.
            </p>"""

        html = f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:520px;margin:0 auto;padding:40px 24px;color:#e8e8f0;background:#0a0a12;border-radius:16px">
            <div style="text-align:center;margin-bottom:32px">
                <svg width="36" height="36" viewBox="0 0 100 100"><path d="M22 65 V44 C22 36 36 36 40 44 V65 M40 44 C44 36 58 36 58 44 V54 C58 63 70 64 73 54" fill="none" stroke="#a855f7" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/><circle cx="75" cy="51" r="8" fill="#a855f7"/><circle cx="75" cy="51" r="3" fill="white"/></svg>
                <h1 style="font-size:22px;font-weight:700;margin:8px 0 4px;color:#e8e8f0">Mengram</h1>
            </div>
            {body_html}
            <hr style="border:none;border-top:1px solid #1a1a2e;margin:28px 0">
            <p style="font-size:12px;color:#55556a;text-align:center">
                <a href="{BASE_URL}/dashboard" style="color:#7c3aed;text-decoration:none">Console</a> &middot;
                <a href="https://docs.mengram.io" style="color:#7c3aed;text-decoration:none">Docs</a> &middot;
                <a href="https://github.com/alibaizhanov/mengram" style="color:#7c3aed;text-decoration:none">GitHub</a>
            </p>
        </div>"""

        try:
            import resend
            resend.api_key = resend_key
            resend.Emails.send({
                "from": EMAIL_FROM,
                "to": email,
                "reply_to": "the.baizhanov@gmail.com",
                "subject": subject,
                "html": html,
            })
            logger.info(f"📧 Quota email sent | user={user_id[:8]} | {action} | {plan} → {next_plan['name'] if next_plan else 'enterprise'}")
        except Exception as e:
            logger.error(f"⚠️  Quota email failed: {e}")

    def _send_quota_warning_email(user_id: str, email: str, plan: str, action: str,
                                  current: int, max_allowed: int):
        """Send one-time email when user hits 80% of quota. Deduped monthly."""
        now = datetime.datetime.now(datetime.timezone.utc)
        drip_type = f"quota_warning_{action}_{now.strftime('%Y-%m')}"

        # Same defensive plan re-check as _send_quota_email — see that function for context.
        try:
            store.cache.invalidate(f"sub:{user_id}")
            fresh_sub = store.get_subscription(user_id)
            fresh_plan = fresh_sub.get("plan", "free") if fresh_sub else "free"
            if fresh_plan != plan and fresh_plan not in ("free",):
                logger.warning(
                    f"🛑 Suppressed {drip_type} for {user_id[:8]} — caller plan={plan}, fresh plan={fresh_plan}"
                )
                return
        except Exception as e:
            logger.warning(f"Quota warning plan re-check failed for {user_id[:8]}: {e}")

        if not store.try_record_drip(email, drip_type, user_id):
            return  # already sent this month

        resend_key = os.environ.get("RESEND_API_KEY")
        if not resend_key:
            return

        action_label = "memory adds" if action == "add" else "searches"
        remaining = max_allowed - current
        pct = int(current / max_allowed * 100)

        next_plan = NEXT_PLAN_INFO.get(plan)
        # Build upgrade button (only if there's a next plan)
        upgrade_html = ""
        if next_plan:
            next_plan_key = {"free": "starter", "starter": "pro", "pro": "growth", "growth": "business"}.get(plan, "starter")
            checkout_token = _sign_checkout_token(user_id, next_plan_key)
            checkout_url = f"{BASE_URL}/checkout?token={checkout_token}"
            next_limit = next_plan["adds"] if action == "add" else next_plan["searches"]
            upgrade_html = f"""
            <div style="text-align:center;margin:24px 0">
                <a href="{checkout_url}"
                   style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">
                    Upgrade to {next_plan['name']} — {next_limit} {action_label}/mo
                </a>
            </div>"""

        html = f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:520px;margin:0 auto;padding:40px 24px;color:#e8e8f0;background:#0a0a12;border-radius:16px">
            <div style="text-align:center;margin-bottom:32px">
                <svg width="36" height="36" viewBox="0 0 100 100"><path d="M22 65 V44 C22 36 36 36 40 44 V65 M40 44 C44 36 58 36 58 44 V54 C58 63 70 64 73 54" fill="none" stroke="#a855f7" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/><circle cx="75" cy="51" r="8" fill="#a855f7"/><circle cx="75" cy="51" r="3" fill="white"/></svg>
                <h1 style="font-size:22px;font-weight:700;margin:8px 0 4px;color:#e8e8f0">Mengram</h1>
            </div>
            <p style="font-size:15px;color:#c8c8d8;line-height:1.6">
                You've used <strong style="color:#f59e0b">{pct}%</strong> of your monthly {action_label}
                — <strong>{remaining:,}</strong> remaining on your {plan} plan.
            </p>
            <div style="background:#12121e;border-radius:8px;padding:4px;margin:20px 0">
                <div style="background:linear-gradient(90deg,#7c3aed,#f59e0b);height:8px;border-radius:6px;width:{pct}%"></div>
            </div>
            <p style="font-size:14px;color:#8888a8;text-align:center;margin:0 0 8px">{current:,} / {max_allowed:,} {action_label} used</p>
            {upgrade_html}
            <p style="font-size:13px;color:#55556a;margin-top:20px">Your limits reset at the start of each month.</p>
            <hr style="border:none;border-top:1px solid #1a1a2e;margin:28px 0">
            <p style="font-size:12px;color:#55556a;text-align:center">
                <a href="{BASE_URL}/dashboard" style="color:#7c3aed;text-decoration:none">Console</a> &middot;
                <a href="https://docs.mengram.io" style="color:#7c3aed;text-decoration:none">Docs</a> &middot;
                <a href="https://github.com/alibaizhanov/mengram" style="color:#7c3aed;text-decoration:none">GitHub</a>
            </p>
        </div>"""

        try:
            import resend
            resend.api_key = resend_key
            resend.Emails.send({
                "from": EMAIL_FROM,
                "to": email,
                "reply_to": "the.baizhanov@gmail.com",
                "subject": f"Heads up: {pct}% of your {action_label} used",
                "html": html,
            })
            logger.info(f"📧 Quota warning sent | user={user_id[:8]} | {action} {current}/{max_allowed} ({pct}%)")
        except Exception as e:
            logger.error(f"⚠️  Quota warning email failed: {e}")

    # ---- Auth middleware ----

    async def auth(request: Request, authorization: str = Header(...)) -> AuthContext:
        """Verify API key, return AuthContext with plan info. Rate limited per plan."""
        key = authorization.replace("Bearer ", "")
        user_id = store.verify_api_key(key)
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Self-hosted mode: unlimited plan, skip subscription lookup
        if DISABLE_EMAIL_VERIFICATION:
            plan = "selfhosted"
        else:
            # Look up subscription (cached 5 min)
            sub = store.get_subscription(user_id)
            plan = sub.get("plan", "free") if sub else "free"
            if plan not in PLAN_QUOTAS:
                plan = "free"
            # Canceled subscription past period end → downgrade to free
            if sub and sub.get("status") == "canceled" and plan != "free":
                period_end = sub.get("current_period_end")
                if period_end:
                    try:
                        end_dt = datetime.datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
                        if end_dt < datetime.datetime.now(datetime.timezone.utc):
                            plan = "free"
                            store.update_subscription(user_id, plan="free")
                    except Exception:
                        pass

        rate_limit = PLAN_QUOTAS[plan]["rate_limit"]

        if not _check_rate_limit(user_id, rate_limit):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({rate_limit} requests/min). Retry in 60 seconds.",
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(rate_limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "60",
                },
            )

        # Get remaining count from Redis for headers
        remaining = rate_limit
        redis_client = getattr(store.cache, '_redis', None) if store else None
        if redis_client:
            try:
                count = redis_client.get(f"rl:{user_id}")
                if count:
                    remaining = max(0, rate_limit - int(count))
            except Exception:
                pass
        request.state.rate_limit = rate_limit
        request.state.rate_remaining = remaining

        # Quota usage from Redis (same keys use_quota writes: qc:{user_id}:{action}:{YYYY-MM})
        _plan_q = PLAN_QUOTAS.get(plan, PLAN_QUOTAS["free"])
        quota_info = {}
        if redis_client:
            try:
                _month = f"{datetime.date.today().year}-{datetime.date.today().month:02d}"
                for _qa, _qk in [("add", "adds"), ("search", "searches")]:
                    _cached = redis_client.get(f"qc:{user_id}:{_qa}:{_month}")
                    _used = int(_cached) if _cached is not None else 0
                    quota_info[_qa] = {"used": _used, "limit": _plan_q.get(_qk, 0)}
            except Exception:
                pass
        if not quota_info:
            quota_info = {
                "add": {"used": 0, "limit": _plan_q.get("adds", 0)},
                "search": {"used": 0, "limit": _plan_q.get("searches", 0)},
            }
        request.state.quota_info = quota_info

        key_prefix = key[:10] if len(key) > 10 else key[:4]
        # Suppress request log for quota-exhausted users (reduces log noise from MCP hooks)
        _skip_log = False
        _path = request.url.path
        if redis_client and plan not in ("business", "selfhosted"):
            try:
                _month = f"{datetime.date.today().year}-{datetime.date.today().month:02d}"
                # Only suppress log if the specific request type is over quota
                if _path.startswith("/v1/add"):
                    _cached = redis_client.get(f"qc:{user_id}:add:{_month}")
                    if _cached is not None and int(_cached) >= PLAN_QUOTAS.get(plan, PLAN_QUOTAS["free"]).get("adds", 0):
                        _skip_log = True
                elif "search" in _path:
                    _cached = redis_client.get(f"qc:{user_id}:search:{_month}")
                    if _cached is not None and int(_cached) >= PLAN_QUOTAS.get(plan, PLAN_QUOTAS["free"]).get("searches", 0):
                        _skip_log = True
            except Exception:
                pass
        if not _skip_log:
            logger.info(f"🔑 {request.method} {request.url.path} | key={key_prefix}... | user={user_id[:8]} | plan={plan}")
        return AuthContext(user_id=user_id, plan=plan, rate_limit=rate_limit)

    # ---- Email helper ----

    def _send_api_key_email(email: str, api_key: str, is_reset: bool = False):
        """Send API key to user via Resend."""
        resend_key = os.environ.get("RESEND_API_KEY")
        if not resend_key:
            logger.info("⚠️  RESEND_API_KEY not set, skipping email")
            return

        try:
            import resend
            resend.api_key = resend_key

            action = "reset" if is_reset else "created"
            subject = f"Your new Mengram API key" if is_reset else "Welcome to Mengram"

            if is_reset:
                html = f"""
            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:520px;margin:0 auto;padding:40px 24px;color:#e8e8f0;background:#0a0a12;border-radius:16px">
                <div style="text-align:center;margin-bottom:32px">
                    <svg width="36" height="36" viewBox="0 0 100 100"><path d="M22 65 V44 C22 36 36 36 40 44 V65 M40 44 C44 36 58 36 58 44 V54 C58 63 70 64 73 54" fill="none" stroke="#a855f7" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/><circle cx="75" cy="51" r="8" fill="#a855f7"/><circle cx="75" cy="51" r="3" fill="white"/></svg>
                    <h1 style="font-size:22px;font-weight:700;margin:8px 0 4px;color:#e8e8f0">Mengram</h1>
                </div>
                <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Your API key has been reset. Old keys are now deactivated.</p>
                <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:20px 0;text-align:center">
                    <p style="color:#8888a8;font-size:12px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px">Your New API Key</p>
                    <code style="font-size:14px;color:#a78bfa;word-break:break-all">{api_key}</code>
                </div>
                <p style="font-size:13px;color:#ef4444;font-weight:600">Save this key — it won't be shown again.</p>
                <hr style="border:none;border-top:1px solid #1a1a2e;margin:28px 0">
                <p style="font-size:12px;color:#55556a;text-align:center">
                    <a href="{BASE_URL}/dashboard" style="color:#7c3aed;text-decoration:none">Console</a> ·
                    <a href="https://docs.mengram.io" style="color:#7c3aed;text-decoration:none">Docs</a> ·
                    <a href="https://github.com/alibaizhanov/mengram" style="color:#7c3aed;text-decoration:none">GitHub</a>
                </p>
            </div>
                """
            else:
                html = f"""
            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:520px;margin:0 auto;padding:40px 24px;color:#e8e8f0;background:#0a0a12;border-radius:16px">
                <div style="margin-bottom:28px">
                    <p style="font-size:15px;color:#c8c8d8;margin:0 0 4px">Hey,</p>
                    <p style="font-size:15px;color:#c8c8d8;margin:0 0 16px;line-height:1.6">Ali here, founder of Mengram. Thanks for signing up!</p>
                    <p style="font-size:15px;color:#e8e8f0;margin:0;line-height:1.6">Your AI agents run 24/7 but forget everything between sessions. <strong style="color:#a855f7">Mengram gives them persistent memory</strong> — facts, events, and learned workflows — that grows smarter with every run.</p>
                </div>

                <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:20px 0;text-align:center">
                    <p style="color:#8888a8;font-size:12px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px">Your API Key</p>
                    <code style="font-size:14px;color:#a78bfa;word-break:break-all">{api_key}</code>
                </div>
                <p style="font-size:13px;color:#ef4444;font-weight:600">Save this key — it won't be shown again.</p>

                <div style="background:#1a0a2e;border:2px solid #7c3aed;border-radius:12px;padding:20px;margin:24px 0;text-align:center">
                    <p style="color:#a78bfa;font-weight:700;font-size:16px;margin:0 0 10px">Try it now — 10 seconds</p>
                    <p style="color:#8888a8;font-size:12px;margin:0 0 12px">Save an agent conversation:</p>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:8px;padding:14px;text-align:left">
                        <code style="font-size:12px;color:#22c55e;word-break:break-all;line-height:1.6">curl -X POST {BASE_URL}/v1/add -H "Authorization: Bearer {api_key}" -H "Content-Type: application/json" -d '{{"messages":[{{"role":"user","content":"Fix the auth timeout bug"}},{{"role":"assistant","content":"Fixed. Token TTL was 5min, changed to 30min."}}],"agent_id":"coding-assistant"}}'</code>
                    </div>
                    <p style="color:#8888a8;font-size:12px;margin:10px 0 0">Recall on the next run:</p>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:8px;padding:14px;margin-top:8px;text-align:left">
                        <code style="font-size:12px;color:#22c55e;word-break:break-all;line-height:1.6">curl -X POST {BASE_URL}/v1/search -H "Authorization: Bearer {api_key}" -H "Content-Type: application/json" -d '{{"query":"auth timeout","agent_id":"coding-assistant"}}'</code>
                    </div>
                </div>

                <div style="text-align:center;margin:24px 0">
                    <a href="{BASE_URL}/dashboard" style="background:#7c3aed;color:white;padding:14px 32px;border-radius:10px;text-decoration:none;font-size:15px;font-weight:700;display:inline-block">Open Dashboard</a>
                </div>

                <div style="margin:24px 0">
                    <p style="font-size:14px;font-weight:600;color:#e8e8f0;margin:0 0 10px">Or use the Python SDK:</p>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:16px;margin:0 0 10px">
                        <code style="color:#22c55e;font-size:13px">pip install mengram-ai</code>
                    </div>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:16px">
                        <pre style="margin:0;font-size:12px;color:#22c55e;white-space:pre-wrap"><code>from mengram import Mengram
m = Mengram("{api_key}")
m.add(messages, agent_id="my-agent")
m.search("query", agent_id="my-agent")</code></pre>
                    </div>
                </div>

                <div style="margin:28px 0">
                    <p style="font-size:13px;color:#c8c8d8;margin:0;line-height:2">
                        <span style="color:#a855f7">→</span> <strong>Agent Memory</strong> — agent_id + run_id scoping, multi-agent isolation<br>
                        <span style="color:#a855f7">→</span> <strong>Procedural Learning</strong> — agents learn which workflows succeed<br>
                        <span style="color:#a855f7">→</span> <strong>7 Integrations</strong> — CrewAI, LangChain, Claude Code, OpenClaw, n8n, MCP, REST
                    </p>
                </div>

                <hr style="border:none;border-top:1px solid #1a1a2e;margin:28px 0">
                <p style="font-size:14px;color:#c8c8d8;margin:0 0 16px">Something not working? Just reply — I read every email.</p>
                <p style="font-size:14px;color:#c8c8d8;margin:0 0 20px">— Ali</p>
                <p style="font-size:12px;color:#55556a;text-align:center">
                    <a href="https://docs.mengram.io" style="color:#7c3aed;text-decoration:none">Docs</a> ·
                    <a href="{BASE_URL}/dashboard" style="color:#7c3aed;text-decoration:none">Dashboard</a> ·
                    <a href="https://github.com/alibaizhanov/mengram" style="color:#7c3aed;text-decoration:none">GitHub</a>
                </p>
            </div>
                """

            resend.Emails.send({
                "from": EMAIL_FROM,
                "to": [email],
                "reply_to": "the.baizhanov@gmail.com",
                "subject": subject,
                "html": html,
            })
            logger.info(f"📧 Email sent to {email} (key {action})")
        except Exception as e:
            logger.error(f"⚠️  Email send failed: {e}")

    # ---- Seed initial memory at signup ----

    FREE_EMAIL_DOMAINS = {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.jp",
        "hotmail.com", "outlook.com", "live.com", "msn.com",
        "aol.com", "icloud.com", "me.com", "mac.com",
        "protonmail.com", "proton.me", "pm.me",
        "mail.com", "zoho.com", "yandex.com", "yandex.ru",
        "tutanota.com", "tuta.io", "fastmail.com",
        "qq.com", "163.com", "126.com", "sina.com",
        "gmx.com", "gmx.de", "web.de", "t-online.de",
        "mail.ru", "rambler.ru", "inbox.ru",
    }

    def _parse_name_from_email(local: str) -> str | None:
        """Try to extract a human name from email local part.
        Returns title-cased name or None if it looks like a username."""
        import re
        # Split on dots, hyphens, underscores
        parts = re.split(r'[._\-]+', local.lower())
        # Filter out parts that are just numbers or single chars
        name_parts = [p for p in parts if len(p) > 1 and not p.isdigit()]
        if len(name_parts) >= 2:
            # Looks like first.last
            return " ".join(p.capitalize() for p in name_parts[:3])
        elif len(name_parts) == 1 and len(name_parts[0]) >= 2:
            # Single word — capitalize it
            return name_parts[0].capitalize()
        return None

    def _seed_initial_memory(user_id: str, email: str):
        """Seed 1-2 entities from signup email so first search isn't empty.
        Runs in background thread. Does not consume quota."""
        import threading

        def _do_seed():
            try:
                local, domain = email.rsplit("@", 1)
                domain = domain.lower()
                name = _parse_name_from_email(local)
                is_personal = domain in FREE_EMAIL_DOMAINS

                embedder = get_embedder()
                entities_to_embed = []  # [(entity_id, chunk_text)]

                # Entity 1: Person
                person_name = name or "User"
                person_facts = [f"Signed up for Mengram on {datetime.date.today().isoformat()}"]
                if not is_personal and domain:
                    person_facts.append(f"Email domain: {domain}")
                entity_id = store.save_entity(
                    user_id=user_id, name=person_name, type="person",
                    facts=person_facts, sub_user_id="default",
                )
                chunk = f"{person_name}: " + ". ".join(person_facts)
                entities_to_embed.append((entity_id, chunk))

                # Entity 2: Company (only for work emails)
                if not is_personal and domain:
                    company = domain.split(".")[0].capitalize()
                    company_facts = [f"{person_name} works at {company}"]
                    comp_id = store.save_entity(
                        user_id=user_id, name=company, type="company",
                        facts=company_facts, sub_user_id="default",
                    )
                    comp_chunk = f"{company}: " + ". ".join(company_facts)
                    entities_to_embed.append((comp_id, comp_chunk))

                # Generate embeddings so search works
                if embedder and entities_to_embed:
                    texts = [chunk for _, chunk in entities_to_embed]
                    embeddings = embedder.embed_batch(texts)
                    for (eid, chunk_text), emb in zip(entities_to_embed, embeddings):
                        store.save_embedding(eid, chunk_text, emb)

                logger.info(f"🌱 Seeded {len(entities_to_embed)} entities for {email}")
            except Exception as e:
                logger.error(f"⚠️  Seed memory failed for {email}: {e}")

        threading.Thread(target=_do_seed, daemon=True).start()

    def _send_verification_email(email: str, code: str):
        """Send 6-digit verification code via Resend."""
        resend_key = os.environ.get("RESEND_API_KEY")
        if not resend_key:
            logger.warning(f"⚠️  RESEND_API_KEY not set — verification code for {email}: {code}")
            return
        try:
            import resend
            resend.api_key = resend_key
            resend.Emails.send({
                "from": EMAIL_FROM,
                "to": [email],
                "reply_to": "the.baizhanov@gmail.com",
                "subject": "Verify your Mengram account",
                "text": (
                    f"Hi,\n\n"
                    f"Thanks for signing up for Mengram — long-term memory for AI agents.\n\n"
                    f"Your one-time sign-in code is:\n\n"
                    f"    {code}\n\n"
                    f"Enter it on the verification page to finish creating your account. "
                    f"The code expires in 10 minutes.\n\n"
                    f"If you did not request this email, you can safely ignore it — "
                    f"someone probably typed your address by mistake. No account will be created without the code.\n\n"
                    f"Need help? Just reply to this email and we'll get back to you.\n\n"
                    f"— The Mengram team\n"
                    f"Console: {BASE_URL}/dashboard\n"
                    f"Docs: https://docs.mengram.io\n"
                    f"GitHub: https://github.com/alibaizhanov/mengram\n"
                ),
                "html": f"""
                <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:520px;margin:0 auto;padding:40px 24px;color:#e8e8f0;background:#0a0a12;border-radius:16px">
                    <div style="text-align:center;margin-bottom:32px">
                        <svg width="36" height="36" viewBox="0 0 100 100"><path d="M22 65 V44 C22 36 36 36 40 44 V65 M40 44 C44 36 58 36 58 44 V54 C58 63 70 64 73 54" fill="none" stroke="#a855f7" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/><circle cx="75" cy="51" r="8" fill="#a855f7"/><circle cx="75" cy="51" r="3" fill="white"/></svg>
                        <h1 style="font-size:22px;font-weight:700;margin:8px 0 4px;color:#e8e8f0">Mengram</h1>
                    </div>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Hi,</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Thanks for signing up for Mengram — long-term memory for AI agents. Use the one-time code below to finish creating your account:</p>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:20px;text-align:center;margin:20px 0;">
                        <span style="font-size:36px;font-weight:700;letter-spacing:10px;color:#a855f7;">{code}</span>
                    </div>
                    <p style="font-size:14px;color:#8888a8;line-height:1.6">Enter this code on the verification page. It expires in 10 minutes.</p>
                    <p style="font-size:13px;color:#55556a;line-height:1.6">If you did not request this email, you can safely ignore it — someone probably typed your address by mistake. No account will be created without the code.</p>
                    <p style="font-size:13px;color:#55556a;line-height:1.6">Need help? Just reply to this email and we'll get back to you.</p>
                    <hr style="border:none;border-top:1px solid #1a1a2e;margin:28px 0">
                    <p style="font-size:12px;color:#55556a;text-align:center">
                        <a href="{BASE_URL}/dashboard" style="color:#7c3aed;text-decoration:none">Console</a> &middot;
                        <a href="https://docs.mengram.io" style="color:#7c3aed;text-decoration:none">Docs</a> &middot;
                        <a href="https://github.com/alibaizhanov/mengram" style="color:#7c3aed;text-decoration:none">GitHub</a>
                    </p>
                </div>
                """,
            })
            logger.info(f"📧 Verification code sent to {email}")
        except Exception as e:
            logger.error(f"⚠️  Verification email failed: {e}")

    def _send_drip_email(email: str, drip_type: str, code: str = None, user_id: str = None, plan: str = None):
        """Send an onboarding drip email."""
        resend_key = os.environ.get("RESEND_API_KEY")
        if not resend_key:
            return
        # Check unsubscribe before sending
        if store.is_email_unsubscribed(email):
            return
        try:
            import resend
            resend.api_key = resend_key

            import urllib.parse as _urlparse
            unsub_url = f"{BASE_URL}/unsubscribe?email={_urlparse.quote(email)}"

            # Common email wrapper
            def _wrap(subject: str, body_html: str):
                return f"""
                <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:520px;margin:0 auto;padding:40px 24px;color:#e8e8f0;background:#0a0a12;border-radius:16px">
                    <div style="text-align:center;margin-bottom:32px">
                        <svg width="36" height="36" viewBox="0 0 100 100"><path d="M22 65 V44 C22 36 36 36 40 44 V65 M40 44 C44 36 58 36 58 44 V54 C58 63 70 64 73 54" fill="none" stroke="#a855f7" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/><circle cx="75" cy="51" r="8" fill="#a855f7"/><circle cx="75" cy="51" r="3" fill="white"/></svg>
                        <h1 style="font-size:22px;font-weight:700;margin:8px 0 4px;color:#e8e8f0">Mengram</h1>
                    </div>
                    {body_html}
                    <hr style="border:none;border-top:1px solid #1a1a2e;margin:28px 0">
                    <p style="font-size:12px;color:#55556a;text-align:center">
                        <a href="{BASE_URL}/dashboard" style="color:#7c3aed;text-decoration:none">Console</a> &middot;
                        <a href="https://docs.mengram.io" style="color:#7c3aed;text-decoration:none">Docs</a> &middot;
                        <a href="https://github.com/alibaizhanov/mengram" style="color:#7c3aed;text-decoration:none">GitHub</a>
                    </p>
                    <p style="font-size:11px;color:#3a3a4a;text-align:center;margin-top:8px">
                        <a href="{unsub_url}" style="color:#3a3a4a;text-decoration:underline">Unsubscribe</a>
                    </p>
                </div>"""

            if drip_type == "completed_24h":
                subject = "Quick start: add your first memory in 30 seconds"
                body = """
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">You signed up for Mengram — here's the fastest way to get started:</p>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:20px 0">
                        <code style="font-size:13px;color:#22c55e">pip install mengram-ai</code>
                    </div>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                        <pre style="margin:0;font-size:13px;color:#a78bfa;white-space:pre-wrap"><code>from mengram import Mengram
m = Mengram("your-api-key")
m.add("I love hiking in the mountains")</code></pre>
                    </div>
                    <p style="font-size:14px;color:#8888a8">That's it — 3 lines to give your AI persistent memory.</p>
                    <p style="font-size:14px;color:#8888a8;margin-top:16px">Prefer zero-code? Run <code style="color:#22c55e">mengram setup</code> for Claude Code hooks, add Mengram to <a href="https://docs.mengram.io/openclaw" style="color:#7c3aed">OpenClaw</a>, or use the <a href="https://docs.mengram.io/mcp-server" style="color:#7c3aed">MCP server</a>.</p>
                    <div style="text-align:center;margin:24px 0">
                        <a href="https://docs.mengram.io/quickstart" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Read quickstart guide</a>
                    </div>"""

            elif drip_type == "completed_72h":
                subject = "5 ways to use Mengram"
                body = """
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Haven't tried Mengram yet? Here are 5 popular ways to get started:</p>
                    <div style="margin:20px 0">
                        <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                            <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">1. Claude Code Hooks</p>
                            <p style="color:#8888a8;font-size:13px;margin:0">Auto-save and auto-recall memory in Claude Code. Run <code style="color:#22c55e">mengram setup</code> to install.</p>
                        </div>
                        <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                            <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">2. OpenClaw Plugin</p>
                            <p style="color:#8888a8;font-size:13px;margin:0">12 tools for AI agents — auto-recall, auto-capture, Graph RAG across all channels.</p>
                        </div>
                        <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                            <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">3. MCP Server — works with Claude, Cursor, Windsurf</p>
                            <p style="color:#8888a8;font-size:13px;margin:0">29 tools to add memory to any AI tool with zero code.</p>
                        </div>
                        <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                            <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">4. Python / JavaScript SDK</p>
                            <p style="color:#8888a8;font-size:13px;margin:0">Build apps with persistent AI memory in a few lines.</p>
                        </div>
                        <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                            <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">5. n8n / REST API</p>
                            <p style="color:#8888a8;font-size:13px;margin:0">Automate memory with workflows or direct API calls.</p>
                        </div>
                    </div>
                    <div style="text-align:center;margin:24px 0">
                        <a href="https://docs.mengram.io" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Explore docs</a>
                    </div>"""

            elif drip_type == "completed_7d":
                subject = "Your memory vault is empty"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Your Mengram vault is still empty. Here's the easiest way to start:</p>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:20px 0">
                        <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">Claude Code (one command)</p>
                        <pre style="margin:0;font-size:13px;color:#22c55e;white-space:pre-wrap"><code>mengram setup</code></pre>
                        <p style="color:#8888a8;font-size:12px;margin:8px 0 0">Auto-saves and auto-recalls memory in every session.</p>
                    </div>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                        <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">Or use the REST API</p>
                        <pre style="margin:0;font-size:13px;color:#22c55e;white-space:pre-wrap"><code>curl -X POST {BASE_URL}/v1/add \\
  -H "Authorization: Bearer YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"messages":[{{"role":"user","content":"I like coffee"}}]}}'</code></pre>
                    </div>
                    <p style="font-size:14px;color:#8888a8">Also works with <a href="https://docs.mengram.io/openclaw" style="color:#7c3aed">OpenClaw</a>, <a href="https://docs.mengram.io/mcp-server" style="color:#7c3aed">MCP</a>, <a href="https://docs.mengram.io/langchain" style="color:#7c3aed">LangChain</a>, and <a href="https://docs.mengram.io/crewai" style="color:#7c3aed">CrewAI</a>.</p>
                    <div style="text-align:center;margin:24px 0">
                        <a href="{BASE_URL}/dashboard" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Open dashboard</a>
                    </div>"""

            elif drip_type == "incomplete_1h":
                subject = "Your verification code is waiting"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">You started signing up for Mengram but haven't verified your email yet. Here's a fresh code:</p>
                    <div style="background:#f5f5f7;padding:16px 24px;border-radius:8px;text-align:center;margin:20px 0">
                        <span style="font-size:32px;font-weight:700;letter-spacing:8px;color:#1a1a2e">{code}</span>
                    </div>
                    <p style="color:#8888a8;font-size:14px">This code expires in 10 minutes. Enter it at <a href="{BASE_URL}/dashboard" style="color:#7c3aed">mengram.io/dashboard</a>.</p>"""

            elif drip_type == "incomplete_24h":
                subject = "Still want to try Mengram?"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">You signed up for Mengram yesterday but never finished verification. Here's one last code:</p>
                    <div style="background:#f5f5f7;padding:16px 24px;border-radius:8px;text-align:center;margin:20px 0">
                        <span style="font-size:32px;font-weight:700;letter-spacing:8px;color:#1a1a2e">{code}</span>
                    </div>
                    <p style="color:#8888a8;font-size:14px">This code expires in 10 minutes. Enter it at <a href="{BASE_URL}/dashboard" style="color:#7c3aed">mengram.io/dashboard</a>.</p>
                    <p style="color:#55556a;font-size:12px;margin-top:16px">This is the last reminder we'll send.</p>"""

            elif drip_type == "added_no_search":
                subject = "You added memories — now try searching them"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">You've been adding memories to Mengram — great start! But you haven't searched yet.</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">The real value kicks in when your AI can <strong style="color:#a78bfa">retrieve</strong> what it learned. Try it:</p>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:20px 0">
                        <pre style="margin:0;font-size:13px;color:#22c55e;white-space:pre-wrap"><code>curl -X POST {BASE_URL}/v1/search \\
  -H "Authorization: Bearer YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"query": "what do I know about..."}}'</code></pre>
                    </div>
                    <p style="font-size:14px;color:#8888a8">Or use the search bar in your <a href="{BASE_URL}/dashboard" style="color:#7c3aed">dashboard</a>.</p>"""

            elif drip_type == "searched_no_add":
                subject = "Your search returned empty — here's why"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">You've been searching Mengram, but your memory vault is empty — that's why you're getting no results.</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Add your first memory and search will start working:</p>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:20px 0">
                        <pre style="margin:0;font-size:13px;color:#22c55e;white-space:pre-wrap"><code>curl -X POST {BASE_URL}/v1/add \\
  -H "Authorization: Bearer YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"messages":[{{"role":"user","content":"I like coffee"}}]}}'</code></pre>
                    </div>
                    <p style="font-size:14px;color:#8888a8">Mengram extracts entities, facts, and relationships — then search finds them semantically.</p>
                    <div style="text-align:center;margin:24px 0">
                        <a href="https://docs.mengram.io/quickstart" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Read quickstart guide</a>
                    </div>"""

            elif drip_type == "churned_7d":
                subject = "Your Mengram memory is waiting"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Hi,</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Your Mengram account has been quiet for a while. Everything ok?</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Your memories are still here — facts, events, and workflows your agents built up. They're ready whenever you are.</p>
                    <div style="text-align:center;margin:24px 0">
                        <a href="{BASE_URL}/dashboard" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Open Dashboard</a>
                    </div>
                    <p style="font-size:13px;color:#8888a8">If you ran into any issues or have feedback, just reply to this email.</p>"""

            elif drip_type == "churned_14d":
                subject = "Your Mengram memories miss you"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Hi,</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">It's been two weeks since your last Mengram activity. Your entities, episodes, and procedures are still intact — but memory works best when it stays fresh.</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Pick up where you left off — open the dashboard, reconnect your tools, or just call the API. Your AI still remembers everything.</p>
                    <div style="text-align:center;margin:24px 0">
                        <a href="{BASE_URL}/dashboard" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Open Dashboard</a>
                    </div>
                    <p style="font-size:13px;color:#8888a8">Questions or feedback? Just reply to this email.</p>"""

            elif drip_type == "churned_30d":
                subject = "Last call — re-activate your Mengram memory"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Hi,</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">It's been a month since you last used Mengram. Your agent's memory is going stale — entities and procedures lose relevance without fresh context.</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">One conversation is all it takes to bring everything back to life. Your data is still here.</p>
                    <div style="text-align:center;margin:24px 0">
                        <a href="{BASE_URL}/dashboard" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Open Dashboard</a>
                    </div>
                    <p style="font-size:13px;color:#8888a8">If Mengram wasn't the right fit, I'd love to hear why — just reply.</p>"""

            elif drip_type == "health_digest_degraded":
                # Day 4 — fires when memory_health row says status != healthy.
                # `code` carries a one-liner summary, `plan` (re-used field) carries the recommendations list (joined).
                health_summary = code or "Retrieval relevance is below the healthy threshold."
                recs = plan or "Consider running deduplication and reviewing recently added content for noise."
                subject = "Your Mengram memory needs attention this week"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Hi,</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">The Memory Health Monitor flagged your retrieval quality this week. Here's the snapshot:</p>
                    <div style="background:#1a1a2e;border:1px solid #2a2a44;border-radius:10px;padding:16px 20px;margin:18px 0;font-family:'JetBrains Mono',Menlo,monospace;font-size:13px;color:#e8e8f0;line-height:1.5">{health_summary}</div>
                    <p style="font-size:14px;color:#c8c8d8;line-height:1.6"><strong>What to do:</strong></p>
                    <p style="font-size:14px;color:#c8c8d8;line-height:1.6;background:#0f0f1a;border-left:3px solid #a855f7;padding:10px 14px;border-radius:4px">{recs}</p>
                    <div style="text-align:center;margin:24px 0">
                        <a href="{BASE_URL}/dashboard" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Open Memory Health</a>
                    </div>
                    <p style="font-size:12px;color:#55556a;line-height:1.5">This email fires when your retrieval quality drops below 0.6 mean cosine relevance over the past week. Healthy users don't get this digest. To disable, reply with "unsubscribe health digest".</p>"""

            elif drip_type == "insights_digest":
                # Weekly Insights digest — fires Mondays after Dream Cycle has had
                # 7 days to populate. `code` carries the new_insights count; `plan`
                # (re-used field) carries a JSON-encoded samples list.
                import json as _json
                import html as _html_esc
                try:
                    samples = _json.loads(plan) if plan else []
                except Exception:
                    samples = []
                # Defensive: count can be a huge LLM-generated number for power
                # users (saw 1014 in dry-run). Cap subject line so it doesn't
                # look like spam.
                try:
                    _count_int = int(code) if code else 0
                except (TypeError, ValueError):
                    _count_int = 0
                if _count_int >= 100:
                    count = f"{_count_int}+"
                elif _count_int > 0:
                    count = str(_count_int)
                else:
                    count = "several"
                subject = f"Mengram refreshed {count} insights about you this week"
                sample_html = ""
                if samples:
                    scope_label = {
                        "entity": "Profile",
                        "cross": "Pattern",
                        "temporal": "Recent",
                    }
                    rows = []
                    for s in samples[:5]:
                        scope = scope_label.get(s.get("scope", ""), s.get("scope", ""))
                        title = _html_esc.escape((s.get("title") or "").strip()[:80])
                        content_raw = (s.get("content") or "").strip()
                        if len(content_raw) > 220:
                            content_raw = content_raw[:217] + "…"
                        content = _html_esc.escape(content_raw)
                        rows.append(
                            f"""<div style="background:#12121e;border:1px solid #2a2a44;border-radius:8px;padding:14px 16px;margin:10px 0">
                                <div style="font-size:11px;color:#a78bfa;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">{scope}</div>
                                <div style="font-size:14px;color:#e8e8f0;font-weight:600;margin-bottom:6px">{title}</div>
                                <div style="font-size:13px;color:#9999b0;line-height:1.5">{content}</div>
                               </div>"""
                        )
                    sample_html = "".join(rows)
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Hi,</p>
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Mengram's Dream Cycle ran this week and refreshed your insight layer. Here's a preview of what surfaced:</p>
                    {sample_html}
                    <div style="text-align:center;margin:24px 0">
                        <a href="{BASE_URL}/dashboard?tab=intelligence" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">See all insights</a>
                    </div>
                    <p style="font-size:13px;color:#8888a8;line-height:1.6">These are derived from facts you've stored in Mengram. The Dream Cycle runs nightly to look for patterns across your knowledge graph — entity summaries, cross-entity themes, and recent shifts.</p>
                    <p style="font-size:12px;color:#55556a;line-height:1.5">You're getting this because Mengram refreshed your insights this week. To stop these digests, reply with "unsubscribe insights digest".</p>"""

            elif drip_type in ("checkout_abandoned_1h", "checkout_abandoned_24h"):
                # Build a fresh HMAC-signed checkout URL (robust — original Paddle URL may expire)
                resume_url = f"{BASE_URL}/dashboard?tab=billing"
                if user_id and plan:
                    token = _sign_checkout_token(user_id, plan)
                    if token:
                        resume_url = f"{BASE_URL}/checkout?token={token}"
                plan_name = {"starter": "Starter", "pro": "Pro", "growth": "Growth", "business": "Business"}.get(plan or "", "paid")
                if drip_type == "checkout_abandoned_1h":
                    subject = f"Finish upgrading to Mengram {plan_name}"
                    body = f"""
                        <p style="font-size:15px;color:#c8c8d8;line-height:1.6">You started upgrading to Mengram {plan_name} but didn't finish checkout.</p>
                        <p style="font-size:15px;color:#c8c8d8;line-height:1.6">One click to pick up where you left off — no need to re-enter anything:</p>
                        <div style="text-align:center;margin:24px 0">
                            <a href="{resume_url}" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Resume checkout</a>
                        </div>
                        <p style="font-size:13px;color:#8888a8">If something went wrong with payment, just reply to this email — happy to help.</p>"""
                else:
                    subject = f"Still thinking about Mengram {plan_name}?"
                    body = f"""
                        <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Yesterday you started upgrading to Mengram {plan_name}. You can still finish — checkout is one click away:</p>
                        <div style="text-align:center;margin:24px 0">
                            <a href="{resume_url}" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Resume checkout</a>
                        </div>
                        <p style="font-size:14px;color:#8888a8">Questions about the plan? Reply to this email and I'll answer personally.</p>
                        <p style="font-size:12px;color:#55556a;margin-top:16px">If you changed your mind, you can ignore this — no more reminders after this one.</p>"""

            else:
                return

            html = _wrap(subject, body)
            payload = {
                "from": EMAIL_FROM,
                "to": [email],
                "subject": subject,
                "html": html,
            }
            if drip_type in ("churned_7d", "churned_14d", "churned_30d"):
                payload["reply_to"] = "the.baizhanov@gmail.com"
            resend.Emails.send(payload)
            logger.info(f"📧 Drip email '{drip_type}' sent to {email}")
        except Exception as e:
            logger.error(f"⚠️ Drip email '{drip_type}' failed for {email}: {e}")

    # ---- Public endpoints ----

    # Landing, pricing, legal, SEO pages, sitemap, llms.txt: cloud/site.py
    app.include_router(build_site_router(__version__))

    # ---- Interactive Playground (unauthenticated, demo account only) ----

    @app.get("/api/playground/search", tags=["System"])
    async def playground_search(q: str = Query(..., min_length=1, max_length=200),
                                request: Request = None):
        """Public playground search — no auth required. Searches demo account only."""
        if not DEMO_USER_ID:
            raise HTTPException(status_code=503, detail="Playground not configured")

        client_ip = request.client.host if request and request.client else "unknown"
        if not _check_playground_rate_limit(client_ip, 30):
            raise HTTPException(
                status_code=429,
                detail="Rate limit reached (30/hour). Sign up for unlimited searches!",
                headers={"Retry-After": "3600"},
            )

        user_id = DEMO_USER_ID
        sub_uid = "default"

        # Cache (5 min — demo data is static)
        import hashlib as _hl
        cache_key = f"playground:{_hl.md5(q.encode('utf-8', errors='replace')).hexdigest()}"
        cached = store.cache.get(cache_key)
        if cached:
            return cached

        embedder = get_embedder()
        emb = None
        if embedder:
            try:
                embs = embedder.embed_batch([q])
                emb = embs[0] if embs else None
            except Exception:
                pass

        if emb is not None:
            semantic = store.search_vector(
                user_id, emb, top_k=10, query_text=q,
                graph_depth=2, sub_user_id=sub_uid)
            episodic = store.search_episodes_vector(
                user_id, emb, top_k=3, sub_user_id=sub_uid, query_text=q)
            procedural = store.search_procedures_vector(
                user_id, emb, top_k=3, sub_user_id=sub_uid, query_text=q)
        else:
            semantic = store.search_text(user_id, q, top_k=10, sub_user_id=sub_uid)
            episodic = []
            procedural = []

        # Clean internal flags
        for r in semantic:
            r.pop("_graph", None)
        semantic = semantic[:5]

        result = {
            "semantic": semantic,
            "episodic": episodic,
            "procedural": procedural,
        }
        store.cache.set(cache_key, result, ttl=300)
        return result

    # ---- Playground Extract (unauthenticated, rate-limited) ----

    @app.post("/api/playground/extract", tags=["System"])
    async def playground_extract(request: Request):
        """Public playground extraction — no auth required. Extracts memory from text without saving."""
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid request")

        text = (data.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "Text is required")
        if len(text) > 2000:
            raise HTTPException(400, "Text must be 2000 characters or less")

        client_ip = request.client.host if request and request.client else "unknown"
        if not _check_playground_rate_limit(client_ip, 5, prefix="pg_extract"):
            raise HTTPException(
                status_code=429,
                detail="Rate limit reached (5 extractions/hour). Sign up for unlimited access!",
                headers={"Retry-After": "3600"},
            )

        try:
            extractor = get_llm()
            conversation = [{"role": "user", "content": text}]
            result = extractor.extract(conversation, existing_context="")
            return {
                "entities": [
                    {"name": e.name, "type": e.entity_type,
                     "facts": [{"fact": f.content, "when": f.event_date} for f in e.facts]}
                    for e in result.entities if e.name
                ],
                "relations": [
                    {"from": r.from_entity, "to": r.to_entity,
                     "type": r.relation_type, "description": r.description}
                    for r in result.relations
                ],
                "episodes": [
                    {"summary": ep.summary, "context": ep.context, "outcome": ep.outcome,
                     "participants": ep.participants, "importance": ep.importance}
                    for ep in result.episodes if ep.summary
                ],
                "procedures": [
                    {"name": p.name, "trigger": p.trigger,
                     "steps": p.steps, "entities": p.entities}
                    for p in result.procedures if p.name
                ],
            }
        except Exception as e:
            logger.error(f"Playground extraction failed: {e}")
            raise HTTPException(500, "Extraction failed. Please try again.")

    # ---- Enterprise Inquiry ----
    @app.post("/enterprise-inquiry")
    async def enterprise_inquiry(request: Request):
        """Handle Enterprise tier contact form."""
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid request")
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip()
        company = (data.get("company") or "").strip()
        team_size = (data.get("team_size") or "").strip()
        message = (data.get("message") or "").strip()
        if not name or not email:
            raise HTTPException(400, "Name and email are required")
        # Send notification email via Resend
        resend_key = os.environ.get("RESEND_API_KEY")
        if resend_key:
            try:
                import resend
                resend.api_key = resend_key
                resend.Emails.send({
                    "from": EMAIL_FROM,
                    "to": ["the.baizhanov@gmail.com"],
                    "reply_to": email,
                    "subject": f"Mengram Enterprise Inquiry — {company or name}",
                    "text": (
                        f"New Enterprise inquiry from mengram.io\n\n"
                        f"Name: {name}\n"
                        f"Email: {email}\n"
                        f"Company: {company or 'Not provided'}\n"
                        f"Team size: {team_size or 'Not provided'}\n\n"
                        f"Message:\n{message or 'No message'}\n"
                    ),
                })
            except Exception as e:
                logger.error(f"Failed to send enterprise inquiry email: {e}")
        else:
            logger.warning(f"Enterprise inquiry from {email} (no RESEND_API_KEY)")
        return {"status": "ok"}

    @app.get("/unsubscribe", response_class=HTMLResponse)
    async def unsubscribe(email: str = Query("")):
        """Unsubscribe from drip emails."""
        html = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Unsubscribe — Mengram</title>
        <style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a12;color:#e8e8f0;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}
        .card{background:#12121e;border:1px solid #1a1a2e;border-radius:16px;padding:48px;text-align:center;max-width:400px}
        h1{font-size:20px;margin:0 0 12px}p{color:#8888a8;font-size:14px;line-height:1.6;margin:0}
        a{color:#7c3aed;text-decoration:none}</style></head><body><div class="card">"""
        if email:
            store.unsubscribe_email(email)
            html += f"<h1>You've been unsubscribed</h1><p>{email} will no longer receive onboarding emails from Mengram.</p>"
            logger.info(f"📧 Unsubscribed: {email}")
        else:
            html += "<h1>Invalid link</h1><p>No email address provided.</p>"
        html += '<p style="margin-top:24px"><a href="https://mengram.io">Back to Mengram</a></p></div></body></html>'
        return html

    @app.get("/extension/download")
    async def download_extension():
        """Download Chrome extension zip."""
        ext_path = Path(__file__).parent / "mengram-chrome-extension.zip"
        if not ext_path.exists():
            raise HTTPException(status_code=404, detail="Extension not available")
        return FileResponse(
            path=str(ext_path),
            filename="mengram-chrome-extension.zip",
            media_type="application/zip"
        )

    @app.get("/v1/me", tags=["System"])
    async def me(ctx: AuthContext = Depends(auth)):
        """Current account info."""
        user_id = ctx.user_id
        email = store.get_user_email(user_id)
        plan = ctx.plan  # already resolved in auth() (selfhosted / cloud plan)
        usage = store.get_all_usage_counts(user_id)
        plan_quotas = PLAN_QUOTAS.get(plan, PLAN_QUOTAS["free"])
        return {
            "email": email,
            "plan": plan,
            "user_id": user_id,
            "usage": usage,
            "quotas": {k: v for k, v in plan_quotas.items() if k != "rate_limit"},
        }

    @app.post("/v1/signup", tags=["System"])
    async def signup(req: SignupRequest, request: Request):
        """Step 1: Send verification code to email."""
        try:
            email = req.validated_email
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid email address")

        client_ip = request.client.host if request.client else "unknown"

        # Honeypot — bots fill this invisible field, real users don't. Respond
        # with the usual success message so bots don't learn they were blocked.
        if req.website.strip():
            logger.warning(f"🤖 Signup honeypot triggered: email={email} ip={client_ip}")
            return {"message": "Verification code sent to your email. Check your inbox."}

        # Reject disposable / throwaway email providers commonly used by bots
        if _is_disposable_email(email):
            logger.warning(f"🚫 Disposable email rejected: email={email} ip={client_ip}")
            raise HTTPException(
                status_code=400,
                detail="Please use a permanent email address. Disposable email providers are not supported."
            )

        # Flag obviously bot-generated email prefixes (gibberish, long digit runs).
        # Log-only for now — don't block, to avoid false positives on real users.
        # Review logs after 2 weeks and enable blocking if 0 false positives.
        if _looks_like_bot_email(email):
            logger.warning(f"🤖 Bot-pattern email flagged (NOT blocked): email={email} ip={client_ip}")

        # Rate limit: 5/min per IP, 3/min per email
        if not _check_rate_limit(f"signup:{client_ip}", 5):
            raise HTTPException(status_code=429, detail="Too many signup attempts. Try again in 60 seconds.")
        if not _check_rate_limit(f"signup_email:{email}", 3):
            raise HTTPException(status_code=429, detail="Too many attempts for this email.")

        existing = store.get_user_by_email(email)
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        # Self-hosted: skip email verification, create account immediately
        if DISABLE_EMAIL_VERIFICATION:
            user_id = store.create_user(email, clean_source(req.source))
            api_key = store.create_api_key(user_id)
            _seed_initial_memory(user_id, email)
            logger.info(f"✅ Account created (email verification disabled) for {email}")
            return {"message": "Account created! Save your API key.", "api_key": api_key}

        # Generate and send 6-digit OTP
        code = f"{secrets.randbelow(900000) + 100000}"
        store.save_email_code(email, code)
        _send_verification_email(email, code)

        return {"message": "Verification code sent to your email. Check your inbox."}

    @app.post("/v1/verify", tags=["System"], response_model=SignupResponse)
    async def verify_signup(req: VerifyRequest, request: Request):
        """Step 2: Verify code, create account, return API key."""
        try:
            email = req.validated_email
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid email address")
        code = req.code.strip()

        # Rate limit: 5/min per email, 20/min per IP
        if not _check_rate_limit(f"verify_signup:{email}", 5):
            raise HTTPException(status_code=429, detail="Too many attempts. Try again in 60 seconds.")
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(f"verify_signup_ip:{client_ip}", 20):
            raise HTTPException(status_code=429, detail="Too many attempts.")

        if not store.verify_email_code(email, code):
            raise HTTPException(status_code=400, detail="Invalid or expired code. Request a new one.")

        # Race condition guard
        existing = store.get_user_by_email(email)
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        user_id = store.create_user(email, clean_source(req.source))
        api_key = store.create_api_key(user_id)
        # Eagerly create free subscription so user isn't stuck in no_sub state
        # (lazy creation in get_subscription only happens on first API call)
        store.get_subscription(user_id)
        _send_api_key_email(email, api_key, is_reset=False)
        _seed_initial_memory(user_id, email)

        return SignupResponse(
            api_key=api_key,
            message="Account created! API key sent to your email. Save it — it won't be shown again."
        )

    @app.post("/v1/reset-key", tags=["System"])
    async def reset_key(req: ResetKeyRequest, request: Request):
        """Step 1: Send verification code to reset API key."""
        try:
            email = req.validated_email
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid email address")

        # Rate limit: 3/min per IP, 3/min per email
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(f"reset:{client_ip}", 3):
            raise HTTPException(status_code=429, detail="Too many reset attempts. Try again in 60 seconds.")
        if not _check_rate_limit(f"reset_email:{email}", 3):
            raise HTTPException(status_code=429, detail="Too many attempts for this email.")

        # Don't reveal whether email exists — always say "code sent"
        user_id = store.get_user_by_email(email)
        if user_id:
            # Self-hosted: skip verification, issue a dashboard key immediately
            # (additive — does not revoke existing keys, incl. a connector token)
            if DISABLE_EMAIL_VERIFICATION:
                new_key = store.create_api_key(user_id, name="dashboard")
                logger.info(f"✅ Dashboard key issued (email verification disabled) for {email}")
                return {"message": "Signed in.", "api_key": new_key}

            code = f"{secrets.randbelow(900000) + 100000}"
            store.save_email_code(email, code)
            _send_verification_email(email, code)

        return {"message": "If this email is registered, a verification code has been sent."}

    @app.post("/v1/reset-key/verify", tags=["System"], response_model=SignupResponse)
    async def verify_reset_key(req: VerifyRequest, request: Request):
        """Step 2: Verify code and get new API key."""
        try:
            email = req.validated_email
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid email address")
        code = req.code.strip()

        # Rate limit: 5/min per email, 20/min per IP
        if not _check_rate_limit(f"verify_reset:{email}", 5):
            raise HTTPException(status_code=429, detail="Too many attempts. Try again in 60 seconds.")
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(f"verify_reset_ip:{client_ip}", 20):
            raise HTTPException(status_code=429, detail="Too many attempts.")

        if not store.verify_email_code(email, code):
            raise HTTPException(status_code=400, detail="Invalid or expired code. Request a new one.")

        user_id = store.get_user_by_email(email)
        if not user_id:
            raise HTTPException(status_code=404, detail="Account not found")

        # Non-destructive sign-in: issue an additional dashboard key without
        # revoking existing keys (e.g. a live Claude-connector token). Rotating
        # or revoking keys is done per-key in Settings, not here.
        new_key = store.create_api_key(user_id, name="dashboard")
        _send_api_key_email(email, new_key, is_reset=False)

        return SignupResponse(
            api_key=new_key,
            message="Signed in. A new dashboard key was created; your other keys stay active."
        )

    # ---- GitHub OAuth ----

    GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")

    @app.get("/auth/github", tags=["System"])
    async def github_login(request: Request):
        """Redirect to GitHub OAuth authorization page."""
        if not GITHUB_CLIENT_ID:
            raise HTTPException(status_code=500, detail="GitHub OAuth not configured")
        # Generate state token to prevent CSRF
        state = secrets.token_urlsafe(32)
        # The state entry doubles as the carrier for attribution. It is already
        # per-attempt, already short-lived and already verified on the way
        # back, so the tag survives the round trip to GitHub without a cookie
        # or a second store. "1" stands in for "no tag" so the truthiness check
        # below keeps working.
        source = clean_source(request.query_params.get("ref")
                               or request.query_params.get("utm_source"))
        store.cache.set(f"github_state:{state}", source or "1", ttl=600)
        github_url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={GITHUB_CLIENT_ID}"
            f"&redirect_uri={BASE_URL}/auth/github/callback"
            f"&scope=user:email"
            f"&state={state}"
        )
        return RedirectResponse(url=github_url)

    @app.get("/auth/github/callback", response_class=HTMLResponse, tags=["System"])
    async def github_callback(code: str = "", state: str = "", error: str = ""):
        """Handle GitHub OAuth callback — create/login user and show API key."""
        import html as _html
        if error:
            return _github_error_page(f"GitHub authorization denied: {_html.escape(error)}")
        if not code or not state:
            return _github_error_page("Missing code or state parameter.")
        if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
            return _github_error_page("GitHub OAuth not configured on server.")

        # Verify CSRF state
        state_value = store.cache.get(f"github_state:{state}")
        if not state_value:
            return _github_error_page("Invalid or expired state. Please try again.")
        # Invalidate state by overwriting with short TTL
        store.cache.set(f"github_state:{state}", "", ttl=1)
        # Read before invalidating, above: "1" means the visit carried no tag.
        github_source = None if state_value == "1" else clean_source(state_value)

        # Exchange code for access token
        import urllib.request
        import urllib.parse
        try:
            token_data = urllib.parse.urlencode({
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            }).encode()
            token_req = urllib.request.Request(
                "https://github.com/login/oauth/access_token",
                data=token_data,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(token_req, timeout=10) as resp:
                token_resp = json.loads(resp.read())
            access_token = token_resp.get("access_token")
            if not access_token:
                return _github_error_page("Failed to get access token from GitHub.")
        except Exception as e:
            logger.error(f"GitHub token exchange failed: {e}")
            return _github_error_page("Failed to communicate with GitHub.")

        # Fetch user email from GitHub API
        try:
            email_req = urllib.request.Request(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Mengram",
                },
            )
            with urllib.request.urlopen(email_req, timeout=10) as resp:
                emails = json.loads(resp.read())
            # Pick primary verified email
            email = None
            for e in emails:
                if e.get("primary") and e.get("verified"):
                    email = e["email"].strip().lower()
                    break
            if not email:
                # Fallback: any verified email
                for e in emails:
                    if e.get("verified"):
                        email = e["email"].strip().lower()
                        break
            if not email:
                return _github_error_page("No verified email found on your GitHub account.")
        except Exception as e:
            logger.error(f"GitHub email fetch failed: {e}")
            return _github_error_page("Failed to fetch email from GitHub.")

        # Create user or reject if already exists
        existing_user_id = store.get_user_by_email(email)
        if existing_user_id:
            return _github_existing_page(email)

        # New user — create account + key
        user_id = store.create_user(email, github_source or "github-oauth")
        api_key = store.create_api_key(user_id, name="github-oauth")
        # Eagerly create free subscription so user isn't stuck in no_sub state
        store.get_subscription(user_id)
        _send_api_key_email(email, api_key, is_reset=False)
        _seed_initial_memory(user_id, email)
        logger.info(f"🐙 GitHub OAuth signup: {email}")

        return _github_success_page(api_key, email)

    def _github_existing_page(email: str) -> str:
        import html as _html
        email = _html.escape(email)
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mengram — Account Exists</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0a0a;color:#e0e0e0;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#141414;border:1px solid #2a2a2a;border-radius:16px;padding:40px;max-width:420px;width:100%;text-align:center}}
h1{{font-size:20px;margin-bottom:8px;color:#e8e8f0}}
p{{color:#888;font-size:14px;margin-bottom:16px}}
.email{{color:#a78bfa;font-weight:600}}
a{{display:block;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:14px;margin:6px 0}}
.dash{{background:#a855f7;color:#fff}}
.dash:hover{{background:#9333ea}}
.reset{{background:#1a1a2e;color:#a78bfa;border:1px solid #2a2a3e}}
.reset:hover{{background:#22223a}}
</style></head><body>
<div class="card">
<h1>Account already exists</h1>
<p>An account with <span class="email">{email}</span> is already registered.</p>
<p>Use your existing API key to log in, or reset it if you lost it.</p>
<a class="dash" href="/dashboard">Go to Console</a>
<a class="reset" href="/dashboard?reset">Lost your key? Reset it →</a>
</div></body></html>"""

    def _github_success_page(api_key: str, email: str) -> str:
        import html as _html
        email = _html.escape(email)
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mengram — Account created</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0a0a;color:#e0e0e0;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
.card{{background:#141414;border:1px solid #2a2a2a;border-radius:16px;padding:40px;max-width:520px;width:100%}}
h1{{font-size:22px;margin-bottom:8px;color:#34d399;text-align:center}}
.sub{{color:#888;font-size:14px;margin-bottom:24px;text-align:center}}
.key-box{{background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:14px;margin:16px 0;display:flex;align-items:center;gap:8px}}
.key-val{{font-family:'JetBrains Mono',monospace;font-size:13px;color:#a78bfa;word-break:break-all;flex:1}}
.key-box button,.step-cmd button{{background:#1a1a2e;border:1px solid #2a2a3e;color:#888;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px;white-space:nowrap}}
.key-box button:hover,.step-cmd button:hover{{background:#22223a}}
.warn{{color:#888;font-size:12px;margin-bottom:20px;text-align:center}}
.steps-title{{font-size:16px;font-weight:600;color:#e8e8f0;margin-bottom:16px}}
.setup-step{{display:flex;align-items:flex-start;gap:12px;margin-bottom:14px}}
.step-num{{background:#a855f7;color:#fff;width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0;margin-top:2px}}
.step-content{{flex:1;min-width:0}}
.step-label{{color:#e8e8f0;font-weight:600;margin-bottom:4px;font-size:14px}}
.step-cmd{{display:flex;align-items:center;background:#0d0d0d;border:1px solid rgba(255,255,255,0.08);border-radius:6px;padding:6px 10px;gap:8px}}
.step-cmd code{{flex:1;font-family:'JetBrains Mono',monospace;font-size:12px;color:#34d399;word-break:break-all}}
.step-tip{{color:#666;font-size:12px;margin-top:4px}}
.bottom-tip{{color:#666;font-size:13px;margin:16px 0;text-align:center}}
.btns{{display:flex;gap:10px;margin-top:16px}}
.btn-pri{{flex:1;padding:10px;background:#a855f7;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;text-decoration:none;text-align:center}}
.btn-pri:hover{{background:#9333ea}}
.btn-sec{{flex:1;padding:10px;background:#1a1a2e;color:#a78bfa;border:1px solid #2a2a3e;border-radius:8px;font-size:14px;text-decoration:none;text-align:center}}
.btn-sec:hover{{background:#22223a}}
</style></head><body>
<div class="card">
<h1>Account created!</h1>
<p class="sub">{email}</p>
<p style="color:#888;font-size:13px;margin-bottom:4px;">Your API key (save it — won't be shown again):</p>
<div class="key-box">
<span class="key-val" id="api-key">{api_key}</span>
<button onclick="cc(this,'{api_key}')">Copy</button>
</div>
<p class="warn">Key also sent to {email}</p>

<p class="steps-title">Get started in 2 steps:</p>

<div class="setup-step">
<span class="step-num">1</span>
<div class="step-content">
<div class="step-label">Install</div>
<div class="step-cmd"><code>pip install mengram-ai</code><button onclick="cx(this)">Copy</button></div>
</div>
</div>

<div class="setup-step">
<span class="step-num">2</span>
<div class="step-content">
<div class="step-label">Setup (saves key + installs hooks)</div>
<div class="step-cmd"><code>mengram setup</code><button onclick="cx(this)">Copy</button></div>
<div class="step-tip">Already have a key? Use: <code>export MENGRAM_API_KEY={api_key}</code></div>
</div>
</div>

<p class="bottom-tip">Restart Claude Code — it now remembers everything across sessions.</p>

<div style="background:linear-gradient(135deg,rgba(168,85,247,0.15),rgba(124,58,237,0.08));border:1px solid rgba(168,85,247,0.3);border-radius:12px;padding:18px;margin-top:20px;">
<p style="font-size:15px;font-weight:600;color:#e8e8f0;margin-bottom:4px;">Choose your plan to activate</p>
<p style="font-size:13px;color:#888;margin-bottom:14px;">Your API key is ready — pick a plan to start using it.</p>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
<a class="btn-pri" href="/dashboard?tab=billing&plan=starter" style="font-size:13px;padding:10px;">Starter $5/mo</a>
<a class="btn-pri" href="/dashboard?tab=billing&plan=pro" style="font-size:13px;padding:10px;">Pro $19/mo</a>
<a class="btn-sec" href="/dashboard?tab=billing&plan=growth" style="font-size:13px;padding:10px;">Growth $59/mo</a>
<a class="btn-sec" href="/dashboard?tab=billing&plan=business" style="font-size:13px;padding:10px;">Business $99/mo</a>
</div>
</div>

<div class="btns" style="margin-top:12px;">
<a class="btn-sec" href="https://docs.mengram.io/claude-code">Setup Guide</a>
</div>
</div>
<script>
localStorage.setItem('mengram_key','{api_key}');
function cc(b,t){{navigator.clipboard.writeText(t);b.textContent='Copied!';setTimeout(()=>b.textContent='Copy',1500)}}
function cx(b){{const c=b.parentElement.querySelector('code').textContent;navigator.clipboard.writeText(c);b.textContent='Copied!';setTimeout(()=>b.textContent='Copy',1500)}}
</script>
</body></html>"""

    def _github_error_page(message: str) -> str:
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mengram — Error</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0a0a;color:#e0e0e0;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#141414;border:1px solid #2a2a2a;border-radius:16px;padding:40px;max-width:420px;width:100%;text-align:center}}
h1{{font-size:20px;color:#ef4444;margin-bottom:12px}}
p{{color:#888;font-size:14px;margin-bottom:20px}}
a{{color:#a855f7;text-decoration:none}}
</style></head><body>
<div class="card">
<h1>Something went wrong</h1>
<p>{message}</p>
<a href="/">← Back to Mengram</a>
</div></body></html>"""

    # ---- API Key Management ----

    @app.get("/v1/keys", tags=["System"])
    async def list_keys(ctx: AuthContext = Depends(auth)):
        """List all API keys for your account."""
        user_id = ctx.user_id
        keys = store.list_api_keys(user_id)
        return {"keys": keys, "total": len(keys)}

    @app.post("/v1/keys", tags=["System"])
    async def create_key(req: dict, ctx: AuthContext = Depends(auth)):
        """Create a new API key with a name."""
        user_id = ctx.user_id
        name = req.get("name", "default")
        if len(name) > 50:
            raise HTTPException(status_code=400, detail="Name too long (max 50 chars)")
        raw_key = store.create_api_key(user_id, name=name)
        return {
            "key": raw_key,
            "name": name,
            "message": "Save this key — it won't be shown again."
        }

    @app.delete("/v1/keys/{key_id}", tags=["System"])
    async def revoke_key(key_id: str, ctx: AuthContext = Depends(auth)):
        """Revoke a specific API key."""
        user_id = ctx.user_id
        # Don't allow revoking the key being used for this request
        keys = store.list_api_keys(user_id)
        active_count = sum(1 for k in keys if k["active"])
        if active_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot revoke your last active key. Create a new one first."
            )
        if store.revoke_api_key(user_id, key_id):
            return {"status": "revoked", "key_id": key_id}
        raise HTTPException(status_code=404, detail="Key not found or already revoked")

    @app.patch("/v1/keys/{key_id}", tags=["System"])
    async def rename_key(key_id: str, req: dict, ctx: AuthContext = Depends(auth)):
        """Rename an API key."""
        user_id = ctx.user_id
        name = req.get("name", "")
        if not name or len(name) > 50:
            raise HTTPException(status_code=400, detail="Name required (max 50 chars)")
        if store.rename_api_key(user_id, key_id, name):
            return {"status": "renamed", "key_id": key_id, "name": name}
        raise HTTPException(status_code=404, detail="Key not found")

    # ---- OAuth (Claude Connectors + ChatGPT Custom GPTs) ----

    @app.get("/oauth/authorize")
    async def oauth_authorize(
        client_id: str = "",
        redirect_uri: str = "",
        state: str = "",
        response_type: str = "code",
        code_challenge: str = "",
        code_challenge_method: str = "",
    ):
        """OAuth authorize page — shows email login. Carries the PKCE challenge
        (RFC 7636) through the login step so the token exchange can verify it."""
        from urllib.parse import quote, urlparse
        import html as _html

        # Refuse before the user is asked for anything — a rejected target
        # should never get as far as showing a Mengram-branded login form.
        _redirect_error = _redirect_uri_error(redirect_uri)
        if _redirect_error:
            return HTMLResponse(
                f"<!DOCTYPE html><meta charset='utf-8'>"
                f"<div style=\"font-family:system-ui;max-width:32rem;margin:15vh auto;"
                f"padding:0 1.5rem;line-height:1.6\">"
                f"<h1 style='font-size:1.25rem'>Sign-in blocked</h1>"
                f"<p>{_html.escape(_redirect_error)}. Mengram will not send an "
                f"authorization code to this destination.</p>"
                f"<p style='color:#666;font-size:.9rem'>If you started this from "
                f"an AI assistant, open the connector settings and try again.</p>"
                f"</div>",
                status_code=400,
            )

        # Name the destination on the card: the one thing that lets someone
        # spot a code being routed somewhere they didn't intend.
        _dest_host = _html.escape((urlparse(redirect_uri).hostname or "") if redirect_uri else "")
        destination_note = (
            f"<p style='color:#888;margin-bottom:24px;font-size:14px'>Connecting your "
            f"memory to <strong style='color:#e0e0e0'>{_dest_host}</strong></p>"
            if _dest_host else
            "<p style='color:#888;margin-bottom:24px;font-size:14px'>"
            "Connect your memory to your AI assistant</p>"
        )
        redirect_uri_encoded = quote(redirect_uri, safe="")
        state_encoded = quote(state, safe="")
        code_challenge_encoded = quote(code_challenge, safe="")
        code_challenge_method_encoded = quote(code_challenge_method, safe="")
        return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mengram — Sign In</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,system-ui,sans-serif; background:#0a0a0a; color:#e0e0e0;
         display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ background:#141414; border:1px solid #2a2a2a; border-radius:16px; padding:40px;
           max-width:400px; width:100%; }}
  h1 {{ font-size:24px; margin-bottom:8px; }}
  p {{ color:#888; margin-bottom:24px; font-size:14px; }}
  input {{ width:100%; padding:12px 16px; background:#1a1a1a; border:1px solid #333;
           border-radius:8px; color:#e0e0e0; font-size:16px; margin-bottom:12px; outline:none; }}
  input:focus {{ border-color:#646cff; }}
  button {{ width:100%; padding:12px; background:#646cff; color:white; border:none;
            border-radius:8px; font-size:16px; cursor:pointer; }}
  button:hover {{ background:#5558dd; }}
  .step {{ display:none; }}
  .step.active {{ display:block; }}
  .error {{ color:#ff4444; font-size:13px; margin-bottom:12px; display:none; }}
  .logo {{ font-size:32px; margin-bottom:16px; }}
</style>
</head><body>
<div class="card">
  <div class="logo"><svg width='34' height='34' viewBox='0 0 100 100'><path d='M22 65 V44 C22 36 36 36 40 44 V65 M40 44 C44 36 58 36 58 44 V54 C58 63 70 64 73 54' fill='none' stroke='#7c3aed' stroke-width='10' stroke-linecap='round' stroke-linejoin='round'/><circle cx='75' cy='51' r='8' fill='#7c3aed'/><circle cx='75' cy='51' r='3' fill='#fff'/></svg></div>
  <h1>Sign in to Mengram</h1>
  {destination_note}

  <div id="step1" class="step active">
    <input type="email" id="email" placeholder="your@email.com" autofocus>
    <div class="error" id="err1"></div>
    <button onclick="sendCode()">Send verification code</button>
  </div>

  <div id="step2" class="step">
    <p id="sentMsg" style="color:#888">Code sent to your email</p>
    <input type="text" id="code" placeholder="Enter 6-digit code" maxlength="6">
    <div class="error" id="err2"></div>
    <button onclick="verifyCode()">Verify & Connect</button>
  </div>
</div>

<script>
const redirectUri = decodeURIComponent("{redirect_uri_encoded}");
const state = decodeURIComponent("{state_encoded}");
const codeChallenge = decodeURIComponent("{code_challenge_encoded}");
const codeChallengeMethod = decodeURIComponent("{code_challenge_method_encoded}");

async function sendCode() {{
  const email = document.getElementById('email').value.trim();
  if (!email) return;
  const res = await fetch('/oauth/send-code', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{email}})
  }});
  const data = await res.json();
  if (data.ok) {{
    document.getElementById('step1').classList.remove('active');
    document.getElementById('step2').classList.add('active');
    document.getElementById('sentMsg').textContent = 'Code sent to ' + email;
  }} else {{
    document.getElementById('err1').textContent = data.error || 'Failed to send code';
    document.getElementById('err1').style.display = 'block';
  }}
}}

async function verifyCode() {{
  const email = document.getElementById('email').value.trim();
  const code = document.getElementById('code').value.trim();
  const res = await fetch('/oauth/verify', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{email, code, redirect_uri: redirectUri, state, code_challenge: codeChallenge, code_challenge_method: codeChallengeMethod}})
  }});
  const data = await res.json();
  if (data.redirect) {{
    window.location.href = data.redirect;
  }} else {{
    document.getElementById('err2').textContent = data.error || 'Invalid code';
    document.getElementById('err2').style.display = 'block';
  }}
}}

document.getElementById('email').addEventListener('keydown', e => {{ if(e.key==='Enter') sendCode(); }});
document.getElementById('code').addEventListener('keydown', e => {{ if(e.key==='Enter') verifyCode(); }});
</script>
</body></html>""")

    @app.post("/oauth/send-code")
    async def oauth_send_code(req: dict, request: Request):
        """Send email verification code for OAuth."""
        email = req.get("email", "").strip().lower()
        if not email:
            return {"ok": False, "error": "Email required"}

        # Rate limit: 3 codes/min per email, 10/min per IP
        if not _check_rate_limit(f"code:{email}", 3):
            return {"ok": False, "error": "Too many attempts. Try again in 60 seconds."}
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(f"code_ip:{client_ip}", 10):
            return {"ok": False, "error": "Too many attempts. Try again in 60 seconds."}

        # Check if user exists, if not create
        user_id = store.get_user_by_email(email)
        if not user_id:
            # No landing page was involved: this account exists because someone
            # added the connector inside their MCP client, which is itself the
            # most precise answer to "where did they come from".
            user_id = store.create_user(email, "oauth-connector")
            store.create_api_key(user_id)

        # Generate and send 6-digit code
        code = f"{secrets.randbelow(900000) + 100000}"
        store.save_email_code(email, code)

        # Send via Resend
        resend_key = os.environ.get("RESEND_API_KEY")
        if resend_key:
            try:
                import resend
                resend.api_key = resend_key
                resend.Emails.send({
                    "from": EMAIL_FROM,
                    "to": [email],
                    "subject": "Mengram verification code",
                    "html": f"<h2>Your code: {code}</h2><p>Expires in 10 minutes.</p>",
                })
            except Exception as e:
                logger.error(f"⚠️ Email send failed: {e}")
                return {"ok": False, "error": "Failed to send email"}
        else:
            logger.warning(f"⚠️ No RESEND_API_KEY configured, cannot send code to {email}")

        return {"ok": True}

    @app.post("/oauth/verify")
    async def oauth_verify(req: dict, request: Request):
        """Verify email code and create OAuth authorization code."""
        email = req.get("email", "").strip().lower()
        code = req.get("code", "").strip()
        redirect_uri = req.get("redirect_uri", "")
        state = req.get("state", "")
        code_challenge = req.get("code_challenge", "")
        code_challenge_method = req.get("code_challenge_method", "")

        # Brute-force protection: 5 attempts/min per email, 20/min per IP
        if not _check_rate_limit(f"verify:{email}", 5):
            return {"error": "Too many attempts. Try again in 60 seconds."}
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(f"verify_ip:{client_ip}", 20):
            return {"error": "Too many attempts. Try again in 60 seconds."}

        # Re-validated here because /oauth/authorize is only the UI and this
        # endpoint is callable directly. Checked before the email code is
        # consumed so a rejected target doesn't burn the user's one-shot code.
        _redirect_error = _redirect_uri_error(redirect_uri)
        if _redirect_error:
            return {"error": _redirect_error}

        if not store.verify_email_code(email, code):
            return {"error": "Invalid or expired code"}

        user_id = store.get_user_by_email(email)
        if not user_id:
            return {"error": "User not found"}

        # Create OAuth authorization code (with PKCE challenge when provided)
        oauth_code = secrets.token_urlsafe(32)
        store.save_oauth_code(oauth_code, user_id, redirect_uri, state,
                              code_challenge=code_challenge or None,
                              code_challenge_method=code_challenge_method or None)

        # Build redirect URL
        separator = "&" if "?" in redirect_uri else "?"
        redirect_url = f"{redirect_uri}{separator}code={oauth_code}&state={state}"

        return {"redirect": redirect_url}

    @app.post("/oauth/token")
    async def oauth_token(
        grant_type: str = Form("authorization_code"),
        code: str = Form(""),
        client_id: str = Form(""),
        client_secret: str = Form(""),
        redirect_uri: str = Form(""),
        code_verifier: str = Form(""),
    ):
        """Exchange OAuth code for access token."""
        if grant_type != "authorization_code":
            raise HTTPException(status_code=400, detail="Unsupported grant_type")

        result = store.verify_oauth_code(code)
        if not result:
            raise HTTPException(status_code=400, detail="Invalid or expired code")

        # Verify redirect_uri matches the one used during authorization
        stored_redirect = result.get("redirect_uri", "")
        if redirect_uri and stored_redirect and redirect_uri != stored_redirect:
            raise HTTPException(status_code=400, detail="redirect_uri mismatch")

        # PKCE verification (RFC 7636) — required whenever a challenge was stored
        # at /authorize (Claude and every OAuth 2.1 client sends one; the legacy
        # ChatGPT flow sends none and skips this branch).
        challenge = result.get("code_challenge")
        if challenge:
            if not code_verifier:
                raise HTTPException(status_code=400, detail="code_verifier required")
            method = (result.get("code_challenge_method") or "plain").upper()
            if method == "S256":
                import hashlib as _hashlib
                import base64 as _base64
                digest = _hashlib.sha256(code_verifier.encode("ascii")).digest()
                computed = _base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            elif method == "PLAIN":
                computed = code_verifier
            else:
                raise HTTPException(status_code=400, detail="Unsupported code_challenge_method")
            if not secrets.compare_digest(computed, challenge):
                raise HTTPException(status_code=400, detail="Invalid code_verifier")

        # Get or create API key for this user
        user_id = result["user_id"]
        api_key = store.create_api_key(user_id, name="oauth-connector")

        return {
            "access_token": api_key,
            "token_type": "Bearer",
            "scope": "read write",
        }

    @app.get("/icon.svg", include_in_schema=False)
    async def brand_icon():
        """Stable URL for the Mengram mark — advertised to MCP clients via
        serverInfo.icons so the connector tile can show it, and reusable as a
        hosted favicon/og asset."""
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
            "<path d='M22 65 V44 C22 36 36 36 40 44 V65 M40 44 C44 36 58 36 58 44 V54 C58 63 70 64 73 54' "
            "fill='none' stroke='#7c3aed' stroke-width='10' stroke-linecap='round' stroke-linejoin='round'/>"
            "<circle cx='75' cy='51' r='8' fill='#7c3aed'/>"
            "<circle cx='75' cy='51' r='3' fill='#fff'/></svg>"
        )
        return HTMLResponse(svg, media_type="image/svg+xml",
                            headers={"Cache-Control": "public, max-age=86400"})

    # ---- OAuth 2.1 discovery + dynamic registration (MCP / Claude Connectors) ----
    # These make the OAuth flow above discoverable by MCP clients (RFC 9728,
    # RFC 8414, RFC 7591). Token validation is already handled by verify_api_key
    # since the access_token issued above IS a Mengram API key.

    _OAUTH_ISSUER = "https://mengram.io"

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    async def oauth_authorization_server_metadata():
        """Authorization Server Metadata (RFC 8414)."""
        return {
            "issuer": _OAUTH_ISSUER,
            "authorization_endpoint": f"{_OAUTH_ISSUER}/oauth/authorize",
            "token_endpoint": f"{_OAUTH_ISSUER}/oauth/token",
            "registration_endpoint": f"{_OAUTH_ISSUER}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "scopes_supported": ["read", "write"],
        }

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/{resource_path:path}", include_in_schema=False)
    async def oauth_protected_resource_metadata(resource_path: str = ""):
        """Protected Resource Metadata (RFC 9728) — points MCP clients at the AS.
        Served at the root and at any resource path suffix Claude probes."""
        return {
            "resource": f"{_OAUTH_ISSUER}/mcp/connector",
            "authorization_servers": [_OAUTH_ISSUER],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["read", "write"],
        }

    @app.post("/oauth/register", status_code=201, include_in_schema=False)
    async def oauth_register(req: dict):
        """Dynamic Client Registration (RFC 7591). Public client + PKCE, so no
        client secret is issued; the authorize/token flow above validates via
        PKCE and redirect_uri, not a client secret."""
        client_id = "mcp_" + secrets.token_urlsafe(16)
        resp = {
            "client_id": client_id,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "redirect_uris": req.get("redirect_uris") or [],
        }
        for k in ("client_name", "client_uri", "scope", "logo_uri"):
            if req.get(k):
                resp[k] = req[k]
        return resp

    @app.get("/health", include_in_schema=False)
    @app.get("/v1/health", tags=["System"])
    async def health(authorization: str = Header(None)):
        """Health check. Returns basic status for unauthenticated, detailed diagnostics for authenticated."""
        result = {"status": "ok", "version": __version__}

        # Only expose detailed diagnostics to authenticated users
        if authorization:
            key = authorization.replace("Bearer ", "")
            user_id = store.verify_api_key(key)
            if user_id:
                result["cache"] = store.cache.stats()
                result["connection"] = {"type": "pool", "max": store._pool.maxconn} if store._pool else {"type": "single"}
                try:
                    with store._cursor() as cur:
                        cur.execute("SELECT COUNT(*) FROM entities WHERE user_id = %s", (user_id,))
                        result["db"] = {"entities": cur.fetchone()[0]}
                        cur.execute("SELECT COUNT(*) FROM facts WHERE entity_id IN (SELECT id FROM entities WHERE user_id = %s)", (user_id,))
                        result["db"]["facts"] = cur.fetchone()[0]
                except Exception as e:
                    result["db"] = {"error": str(e)}

        return result

    def _run_extraction_pipeline(user_id, sub_uid, conversation, metadata,
                                 expiration_date, job_id, plan, prompt_version=None):
        """Shared extraction pipeline used by /v1/add and /v1/add_file."""
        created = []
        try:
            # ---- Capture boundary: enforced BEFORE extraction/persistence ----
            # Deterministic, server-side. Empty policy = capture everything.
            capture_policy = {}
            try:
                capture_policy = store.get_capture_policy(user_id)
            except Exception as e:
                logger.error(f"⚠️ Capture policy fetch failed: {e}")
            src = (metadata or {}).get("source")
            allow_sources = capture_policy.get("allow_sources") or []
            deny_sources = capture_policy.get("deny_sources") or []
            if (allow_sources and src not in allow_sources) or (src and src in deny_sources):
                logger.info(f"🚫 Capture policy: skipped add for user={user_id[:8]} source={src}")
                if job_id:
                    store.complete_job(job_id,
                                       result={"entities": [], "skipped_by_policy": True, "source": src})
                return created
            _deny_keywords = store._compile_capture_policy(capture_policy)
            _policy_dropped = 0

            extractor = get_llm()
            from concurrent.futures import ThreadPoolExecutor, as_completed

            # Get existing entities context for smarter extraction
            existing_context = ""
            try:
                existing_context = store.get_existing_context(user_id, sub_user_id=sub_uid)
            except Exception as e:
                logger.error(f"⚠️ Context fetch failed: {e}")

            # ---- Windowed extraction: extract per 12-message window ----
            WINDOW_SIZE = 12  # 6 user+assistant exchanges per window
            all_episodes = []
            all_procedures = []
            all_entities = []  # for smart triggers at end
            embedding_queue = []  # [(entity_id, chunks)]

            for win_start in range(0, max(len(conversation), 1), WINDOW_SIZE):
                window = conversation[win_start:win_start + WINDOW_SIZE]
                if not window:
                    break

                win_extraction = extractor.extract(window, existing_context=existing_context,
                                                     prompt_version=prompt_version)
                all_episodes.extend(win_extraction.episodes)
                all_procedures.extend(win_extraction.procedures)
                all_entities.extend(win_extraction.entities)

                # -- Conflict resolution for this window's entities --
                conflict_tasks = []
                for entity in win_extraction.entities:
                    if not entity.name:
                        continue
                    existing_id = store.get_entity_id(user_id, entity.name, sub_user_id=sub_uid)
                    if existing_id and entity.facts:
                        conflict_tasks.append((entity, existing_id))

                conflict_results = {}
                if conflict_tasks:
                    def _check_conflicts(entity, existing_id):
                        try:
                            plain_facts = [str(f.content) if hasattr(f, 'content') else str(f)
                                           for f in entity.facts]
                            archived = store.archive_contradicted_facts(
                                existing_id, plain_facts, extractor.llm)
                            return entity.name, archived
                        except Exception as e:
                            logger.error(f"⚠️ Conflict check failed for {entity.name}: {e}")
                            return entity.name, []

                    with ThreadPoolExecutor(max_workers=5) as pool:
                        futures = [pool.submit(_check_conflicts, ent, eid)
                                   for ent, eid in conflict_tasks]
                        for future in as_completed(futures):
                            name, archived = future.result()
                            conflict_results[name] = archived

                # -- Save this window's entities immediately --
                for entity in win_extraction.entities:
                    name = entity.name
                    if not name:
                        continue

                    entity_relations = []
                    for rel in win_extraction.relations:
                        if rel.from_entity == name:
                            entity_relations.append({
                                "target": rel.to_entity,
                                "type": rel.relation_type,
                                "description": rel.description,
                                "direction": "outgoing",
                            })
                        elif rel.to_entity == name:
                            entity_relations.append({
                                "target": rel.from_entity,
                                "type": rel.relation_type,
                                "description": rel.description,
                                "direction": "incoming",
                            })

                    entity_knowledge = []
                    for k in win_extraction.knowledge:
                        if k.entity == name:
                            entity_knowledge.append({
                                "type": k.knowledge_type,
                                "title": k.title,
                                "content": k.content,
                                "artifact": k.artifact,
                            })

                    fact_strings = []
                    fact_dates = {}
                    for f in entity.facts:
                        if hasattr(f, 'content'):
                            fc = f.content if isinstance(f.content, str) else str(f.content)
                            fact_strings.append(fc)
                            if f.event_date:
                                fact_dates[fc] = f.event_date
                        else:
                            fact_strings.append(str(f))

                    # Capture boundary: drop facts (and matching knowledge) that
                    # hit the deny policy — before anything is persisted.
                    if _deny_keywords:
                        fact_strings, _dropped_f = store.apply_capture_policy_to_facts(
                            fact_strings, _deny_keywords)
                        _policy_dropped += len(_dropped_f)
                        if entity_knowledge:
                            kept_k = []
                            for k in entity_knowledge:
                                blob = f"{k.get('title', '')} {k.get('content', '')}"
                                _, kd = store.apply_capture_policy_to_facts([blob], _deny_keywords)
                                if kd:
                                    _policy_dropped += 1
                                else:
                                    kept_k.append(k)
                            entity_knowledge = kept_k
                        # Nothing left worth saving for a brand-new entity → skip it.
                        if not fact_strings and not entity_knowledge and not entity_relations:
                            existing_id = store.get_entity_id(user_id, name, sub_user_id=sub_uid)
                            if not existing_id:
                                continue

                    archived = conflict_results.get(name)
                    if archived:
                        store.fire_webhooks(user_id, "memory_update", {
                            "entity": name,
                            "archived_facts": archived,
                            "new_facts": fact_strings
                        })

                    # Heuristic fallback: if LLM returned unknown/empty type, try to infer
                    etype = entity.entity_type
                    if not etype or etype == "unknown":
                        etype = store.infer_entity_type(name, fact_strings) or "unknown"

                    try:
                        entity_id = store.save_entity(
                            user_id=user_id,
                            name=name,
                            type=etype,
                            facts=fact_strings,
                            relations=entity_relations,
                            knowledge=entity_knowledge,
                            metadata=metadata if metadata else None,
                            expires_at=expiration_date,
                            sub_user_id=sub_uid,
                            fact_dates=fact_dates,
                        )
                    except Exception as e:
                        logger.warning(f"⚠️ Entity save failed for '{name}': {e}")
                        continue
                    created.append(name)

                    # Chunks cover the entity's full current state, not just
                    # this conversation's facts — the embedding set is replaced
                    # wholesale, so anything left out stops being searchable.
                    try:
                        embedding_queue.append((entity_id, store.build_entity_chunks(
                            entity_id, name, summarize=_summarize_for_embedding)))
                    except Exception as e:
                        logger.warning(f"⚠️ Chunk build failed for '{name}': {e}")

                # -- Refresh context for next window (includes just-saved entities) --
                if win_start + WINDOW_SIZE < len(conversation):
                    try:
                        existing_context = store.get_existing_context(
                            user_id, sub_user_id=sub_uid)
                    except Exception:
                        pass

            # ---- Collect ALL embeddings: entities + conversation + episodes + procedures ----
            # Single batch API call instead of 4+ separate calls
            embedder = get_embedder()
            embed_items = []  # [(save_fn, text)]

            # Entity embeddings
            # Only chunks that aren't embedded yet go to the API; chunks that
            # dropped out of the entity are retired after the batch succeeds.
            stale_by_entity = {}
            if embedder and embedding_queue:
                _dims = getattr(embedder, "dimensions", 1536)
                for entity_id, chunks in embedding_queue:
                    try:
                        existing = store.get_embedded_chunk_texts(entity_id, _dims)
                    except Exception as e:
                        logger.warning(f"⚠️ Embedding lookup failed for {entity_id}: {e}")
                        existing = set()
                    wanted = set(chunks)
                    stale = [t for t in existing if t not in wanted]
                    if stale:
                        stale_by_entity[entity_id] = stale
                    for chunk in chunks:
                        if chunk not in existing:
                            embed_items.append(("entity", entity_id, chunk))

            # Raw conversation chunk
            conv_chunk_text = None
            conv_chunk_id = None
            try:
                conv_chunk_text = "\n".join(
                    f"{m.get('role','user')}: {m.get('content','')}"
                    for m in conversation
                )[:4000]
                conv_chunk_id = store.save_conversation_chunk(
                    user_id, conv_chunk_text, sub_user_id=sub_uid)
                if embedder and conv_chunk_text:
                    embed_items.append(("chunk", conv_chunk_id, conv_chunk_text[:2000]))
            except Exception as e:
                logger.error(f"⚠️ Raw chunk save failed: {e}")

            # Save episodes + collect their embedding texts
            episodes_created = 0
            episodes_linked = 0
            episode_embed_map = {}  # episode_id -> (ep, ep_text)
            for ep in all_episodes:
                if not ep.summary:
                    continue
                if _deny_keywords:
                    ep_blob = f"{ep.summary} {ep.context or ''} {ep.outcome or ''}"
                    _, ep_drop = store.apply_capture_policy_to_facts([ep_blob], _deny_keywords)
                    if ep_drop:
                        _policy_dropped += 1
                        continue
                try:
                    episode_id = store.save_episode(
                        user_id=user_id,
                        summary=ep.summary,
                        context=ep.context,
                        outcome=ep.outcome,
                        participants=ep.participants,
                        emotional_valence=ep.emotional_valence,
                        importance=ep.importance,
                        metadata=metadata if metadata else None,
                        expires_at=expiration_date,
                        sub_user_id=sub_uid,
                        happened_at=getattr(ep, 'happened_at', None),
                    )
                    ep_text = f"{ep.summary}. {ep.context or ''} {ep.outcome or ''}"[:2000]
                    if embedder:
                        embed_items.append(("episode", episode_id, ep_text))
                        episode_embed_map[episode_id] = (ep, ep_text)
                    episodes_created += 1
                except Exception as e:
                    logger.error(f"⚠️ Episode save failed: {e}")

            # Save procedures + collect their embedding texts
            procedures_created = 0
            for pr in all_procedures:
                if not pr.name or not pr.steps:
                    continue
                if _deny_keywords:
                    pr_blob = pr.name + " " + " ".join(
                        (s.get("action", "") + " " + s.get("detail", "")) if isinstance(s, dict) else str(s)
                        for s in pr.steps)
                    _, pr_drop = store.apply_capture_policy_to_facts([pr_blob], _deny_keywords)
                    if pr_drop:
                        _policy_dropped += 1
                        continue
                try:
                    # A near-duplicate of a proven procedure is kept as it is:
                    # its vector stays too, so nothing below re-embeds it.
                    proc_id, proc_action = store.save_extracted_procedure(
                        user_id=user_id,
                        name=pr.name,
                        trigger_condition=pr.trigger,
                        steps=pr.steps,
                        entity_names=pr.entities,
                        metadata=metadata if metadata else None,
                        expires_at=expiration_date,
                        sub_user_id=sub_uid,
                    )
                    if proc_action == "kept":
                        continue
                    if embedder:
                        steps_summary = "; ".join(
                            (s.get("action", "") if isinstance(s, dict) else str(s)) for s in pr.steps[:10]
                        )
                        pr_text = f"{pr.name}. {pr.trigger or ''}. Steps: {steps_summary}"
                        embed_items.append(("procedure", proc_id, pr_text))
                    procedures_created += 1
                except Exception as e:
                    logger.error(f"⚠️ Procedure save failed: {e}")

            # ---- Single batch embed call for ALL items ----
            episode_embeddings = {}  # episode_id -> embedding vector
            if embedder and embed_items:
                all_texts = [item[2] for item in embed_items]
                all_embeddings = embedder.embed_batch(all_texts)
                if len(all_embeddings) != len(embed_items):
                    # zip() would silently drop the tail and leave entities with
                    # a partial embedding set; keep the existing one instead.
                    logger.error(
                        f"⚠️ Embedder returned {len(all_embeddings)} vectors for "
                        f"{len(embed_items)} chunks — skipping embedding update")
                    all_embeddings = []

                # A procedure's text is rewritten wholesale, so its old vector
                # goes only once the replacement is in hand — deleting up-front
                # left it unsearchable whenever the embed call failed.
                if all_embeddings:
                    for item_type, item_id, _text in embed_items:
                        if item_type == "procedure":
                            store.delete_procedure_embeddings(item_id)

                for (item_type, item_id, text), emb in zip(embed_items, all_embeddings):
                    if item_type == "entity":
                        store.save_embedding(item_id, text, emb)
                    elif item_type == "chunk":
                        store.save_chunk_embedding(item_id, text, emb)
                    elif item_type == "episode":
                        store.save_episode_embedding(item_id, text, emb)
                        episode_embeddings[item_id] = emb
                    elif item_type == "procedure":
                        store.save_procedure_embedding(item_id, text, emb)

            # Retire chunks the entity no longer has (archived facts, dropped
            # relations). Runs even when nothing new needed embedding.
            for entity_id, stale in stale_by_entity.items():
                try:
                    store.delete_embeddings_for_texts(entity_id, stale)
                except Exception as e:
                    logger.warning(f"⚠️ Stale embedding cleanup failed for {entity_id}: {e}")

            # ---- Episode auto-linking (uses pre-computed embeddings) ----
            for episode_id, (ep, ep_text) in episode_embed_map.items():
                ep_embedding = episode_embeddings.get(episode_id)
                if not ep_embedding:
                    continue
                try:
                    from cloud.evolution import EvolutionEngine

                    similar_procs = store.search_procedures_vector(
                        user_id, ep_embedding, top_k=3, sub_user_id=sub_uid)

                    ep_full_text = f"{ep.summary}. {ep.context or ''} {ep.outcome or ''}"
                    best_proc = None
                    best_score = 0.0

                    for sp in (similar_procs or []):
                        proc_text = f"{sp['name']}. {sp.get('trigger_condition') or ''}. "
                        proc_text += "; ".join(
                            (s.get("action", "") if isinstance(s, dict) else str(s)) for s in (sp.get("steps") or [])[:10]
                        )
                        score = EvolutionEngine.compute_link_score(
                            vector_similarity=sp["score"],
                            episode_participants=ep.participants or [],
                            procedure_entity_names=sp.get("entity_names") or [],
                            episode_text=ep_full_text,
                            procedure_text=proc_text,
                        )
                        if score > best_score:
                            best_score = score
                            best_proc = sp

                    if best_proc and best_score >= 0.55:
                        store.link_episodes_to_procedure(
                            [episode_id], best_proc["id"])

                        is_failure = EvolutionEngine.is_failure_episode(
                            ep.emotional_valence,
                            outcome=ep.outcome or "",
                            summary=ep.summary,
                            context=ep.context or "",
                        )
                        if is_failure and plan not in ("free", "starter"):
                            # Record the failure on the version that failed
                            # before revising it. This path revised procedures
                            # 1,810 times in production and never once wrote
                            # the failure down, so the ledger we build the
                            # whole feature on showed six failures in ten
                            # thousand rows — and a revision inherited a prior
                            # from a predecessor whose record was empty by
                            # construction.
                            # Nobody calls the feedback tool, so the step has
                            # to be read out of what was already written. The
                            # failure text usually names it outright.
                            failed_step = EvolutionEngine.infer_failed_step(
                                best_proc.get("steps") or [],
                                f"{ep.summary or ''} {ep.context or ''} {ep.outcome or ''}")
                            try:
                                store.procedure_feedback(
                                    user_id, best_proc["id"], success=False,
                                    sub_user_id=sub_uid,
                                    failed_at_step=failed_step)
                            except Exception as e:
                                logger.warning(f"⚠️ Failure not recorded for "
                                               f"{best_proc.get('name')}: {e}")
                            evo = EvolutionEngine(store, embedder, extractor.llm)
                            evo_result = evo.evolve_on_failure(
                                user_id, best_proc["id"], episode_id,
                                ep.context or ep.summary,
                                sub_user_id=sub_uid,
                                failed_at_step=failed_step)
                            if evo_result:
                                logger.info(
                                    f"🔄 Auto-evolved '{best_proc['name']}' "
                                    f"v{evo_result['old_version']}→v{evo_result['new_version']} "
                                    f"from episode")
                                store.create_procedure_evolved_trigger(
                                    user_id=user_id,
                                    procedure_name=best_proc["name"],
                                    old_version=evo_result["old_version"],
                                    new_version=evo_result["new_version"],
                                    change_description=evo_result.get("change_description", ""),
                                    procedure_id=evo_result["new_procedure_id"],
                                    sub_user_id=sub_uid,
                                )
                                evo.suggest_cross_procedure_updates(
                                    user_id,
                                    evo_result["new_procedure_id"],
                                    evo_result.get("change_description", ""),
                                    sub_user_id=sub_uid,
                                )
                        else:
                            store.procedure_feedback(
                                user_id, best_proc["id"], success=True, sub_user_id=sub_uid)

                        episodes_linked += 1
                except Exception as e:
                    logger.error(f"⚠️ Episode auto-link failed: {e}")

            store.log_usage(user_id, "add")

            # Invalidate search cache — fresh data available
            store.cache.invalidate(f"search:{user_id}:{sub_uid}")
            store.cache.invalidate(f"searchall:{user_id}:{sub_uid}")

            _policy_note = f", policy_dropped={_policy_dropped}" if _policy_dropped else ""
            logger.info(f"✅ Background add complete for {user_id} "
                       f"(entities={len(created)}, episodes={episodes_created}, "
                       f"procedures={procedures_created}, linked={episodes_linked}{_policy_note})")
            store.complete_job(job_id, {
                "created": created,
                "count": len(created),
                "episodes": episodes_created,
                "procedures": procedures_created,
                "episodes_linked": episodes_linked,
                "dropped_by_policy": _policy_dropped,
            })

            # ---- Post-completion tasks (fire-and-forget, don't block job) ----
            import threading as _thr
            def _post_completion():
                # Auto entity merge — lightweight SQL, no LLM, all plans
                try:
                    store._auto_merge_duplicate_entities(user_id, sub_uid)
                except Exception as e:
                    logger.warning(f"⚠️ Auto entity merge failed: {e}")

                try:
                    # Auto-reflection
                    if store.should_reflect(user_id, sub_user_id=sub_uid):
                        plan_quotas_local = PLAN_QUOTAS.get(plan, PLAN_QUOTAS["free"])
                        max_reflects = plan_quotas_local.get("reflects", 0)
                        try:
                            store.check_and_increment(user_id, "reflect", max_reflects)
                            logger.info(f"✨ Auto-reflection triggered for {user_id}")
                            extractor2 = get_llm()
                            store.generate_reflections(user_id, extractor2.llm, sub_user_id=sub_uid)
                        except ValueError:
                            logger.info(f"⏭️ Auto-reflection skipped (reflect quota reached) for {user_id}")
                except Exception as e:
                    logger.error(f"⚠️ Auto-reflection failed: {e}")

                try:
                    add_count = store.get_usage_count(user_id, "add")
                    if add_count > 0 and add_count % 5 == 0 and plan not in ("free", "starter"):
                        plan_quotas_local = PLAN_QUOTAS.get(plan, PLAN_QUOTAS["free"])
                        max_agents = plan_quotas_local.get("agents", 0)
                        try:
                            store.check_and_increment(user_id, "agent", max_agents)
                            logger.info(f"🤖 Auto-agents triggered (add #{add_count}) for {user_id}")
                            agent_llm = get_llm()
                            store.run_curator_agent(user_id, agent_llm.llm, auto_fix=True, sub_user_id=sub_uid)
                            store.run_connector_agent(user_id, agent_llm.llm, sub_user_id=sub_uid)
                        except ValueError:
                            logger.info(f"⏭️ Auto-agents skipped (agent quota reached) for {user_id}")
                except Exception as e:
                    logger.error(f"⚠️ Auto-agents failed: {e}")

                # Auto-reclassify unknown entities (all plans, every 20th add, no quota cost)
                try:
                    add_count_rc = store.get_usage_count(user_id, "add")
                    if add_count_rc > 0 and add_count_rc % 20 == 0:
                        rc_llm = get_llm()
                        store.reclassify_unknown_entities(user_id, rc_llm.llm, sub_user_id=sub_uid)
                except Exception as e:
                    logger.error(f"⚠️ Auto-reclassify failed: {e}")

                if plan not in ("free", "starter"):
                    try:
                        tc = 0
                        tc += store.detect_reminder_triggers(user_id, sub_user_id=sub_uid)
                        for entity in all_entities:
                            if entity.name and entity.facts:
                                plain_facts = [f.content if hasattr(f, 'content') else str(f)
                                               for f in entity.facts]
                                tc += store.detect_contradiction_triggers(
                                    user_id, plain_facts, entity.name, sub_user_id=sub_uid
                                )
                        tc += store.detect_pattern_triggers(user_id, sub_user_id=sub_uid)
                        if tc > 0:
                            logger.info(f"🧠 Smart triggers created: {tc} for {user_id}")
                    except Exception as e:
                        logger.error(f"⚠️ Smart triggers failed: {e}")

                if episodes_created > 0:
                    try:
                        from cloud.evolution import EvolutionEngine
                        evo_engine = EvolutionEngine(store, embedder, extractor.llm)
                        evo_result = evo_engine.detect_and_create_from_episodes(user_id, sub_user_id=sub_uid)
                        if evo_result:
                            logger.info(f"🔄 Auto-created procedure '{evo_result['name']}' "
                                       f"from {evo_result['source_episode_count']} episodes")
                            store.create_procedure_evolved_trigger(
                                user_id=user_id,
                                procedure_name=evo_result["name"],
                                old_version=0,
                                new_version=1,
                                change_description=f"Auto-created from {evo_result['source_episode_count']} similar episodes",
                                procedure_id=evo_result["procedure_id"],
                                sub_user_id=sub_uid,
                            )
                    except Exception as e:
                        logger.error(f"⚠️ Experience-driven procedure detection failed: {e}")

            _thr.Thread(target=_post_completion, daemon=True).start()
        except Exception as e:
            logger.error(f"❌ Background add failed: {e}")
            store.fail_job(job_id, str(e))

    # ---- Protected endpoints ----

    @app.post("/v1/add", tags=["Memory"])
    async def add(req: AddRequest, sub_user_id: str | None = Query(None), ctx: AuthContext = Depends(auth)):
        """
        Add memories from conversation.
        Returns immediately with job_id, processes in background.
        """
        user_id = ctx.user_id
        sub_uid = _resolve_sub_user(req.user_id, sub_user_id)

        # Dry run: extract and return preview without saving. Metered like a
        # real add — it runs the same LLM extraction, so leaving it free made
        # /v1/add an unlimited extraction API for anyone passing dry_run.
        if req.dry_run:
            use_quota(ctx, "add")
            extractor = get_llm()
            existing_context = ""
            try:
                existing_context = store.get_existing_context(user_id, sub_user_id=sub_uid)
            except Exception:
                pass
            conversation = [{"role": m.role, "content": _sanitize_text(m.content)} for m in req.messages]
            dry_prompt = "v1" if (req.agent_mode or req.agent_id) else req.prompt_version
            result = extractor.extract(conversation, existing_context=existing_context,
                                       prompt_version=dry_prompt)
            return {
                "dry_run": True,
                "extraction": {
                    "entities": [
                        {"name": e.name, "type": e.entity_type,
                         "facts": [{"fact": f.content, "when": f.event_date} for f in e.facts]}
                        for e in result.entities if e.name
                    ],
                    "relations": [
                        {"from": r.from_entity, "to": r.to_entity,
                         "type": r.relation_type, "description": r.description}
                        for r in result.relations
                    ],
                    "episodes": [
                        {"summary": ep.summary, "context": ep.context, "outcome": ep.outcome,
                         "participants": ep.participants, "importance": ep.importance}
                        for ep in result.episodes if ep.summary
                    ],
                    "procedures": [
                        {"name": p.name, "trigger": p.trigger,
                         "steps": p.steps, "entities": p.entities}
                        for p in result.procedures if p.name
                    ],
                }
            }

        use_quota(ctx, "add")  # atomic check+increment before background processing
        import threading

        # Enforce sub-user limit per plan
        if sub_uid != "default":
            plan_quotas = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
            max_sub_users = plan_quotas.get("sub_users", 3)
            if max_sub_users != -1:
                distinct_sub_users = store.count_distinct_sub_users(user_id)
                # Check if this sub_user_id is new (not already tracked)
                if distinct_sub_users >= max_sub_users:
                    known = store.is_known_sub_user(user_id, sub_uid)
                    if not known:
                        raise HTTPException(status_code=402, detail={
                            "error": "quota_exceeded", "action": "sub_users",
                            "limit": max_sub_users, "used": distinct_sub_users, "plan": ctx.plan,
                            "message": f"Sub-user limit reached ({max_sub_users}). Upgrade your plan.",
                            "upgrade_url": f"{BASE_URL}/#pricing",
                        })
        job_id = store.create_job(user_id, "add")
        # Build metadata from categories + provenance
        metadata = {}
        if req.agent_id:
            metadata["agent_id"] = req.agent_id
        if req.run_id:
            metadata["run_id"] = req.run_id
        if req.app_id:
            metadata["app_id"] = req.app_id
        if req.source:
            metadata["source"] = req.source
        if req.metadata:
            metadata.update(req.metadata)

        # agent_id present or agent_mode=True → extract from all speakers (v1)
        effective_prompt_version = "v1" if (req.agent_mode or req.agent_id) else req.prompt_version

        def process_in_background():
            _run_extraction_pipeline(
                user_id=user_id,
                sub_uid=sub_uid,
                conversation=[{"role": m.role, "content": _sanitize_text(m.content)} for m in req.messages],
                metadata=metadata,
                expiration_date=req.expiration_date,
                job_id=job_id,
                plan=ctx.plan,
                prompt_version=effective_prompt_version,
            )


        threading.Thread(target=process_in_background, daemon=True).start()

        from starlette.responses import JSONResponse
        return JSONResponse(status_code=202, content={
            "status": "accepted",
            "message": "Processing in background. Memories will appear shortly.",
            "job_id": job_id,
        })

    @app.post("/v1/add_text", tags=["Memory"])
    async def add_text(req: AddTextRequest, sub_user_id: str | None = Query(None), ctx: AuthContext = Depends(auth)):
        """Add memories from plain text (wraps into a single user message)."""
        add_req = AddRequest(
            messages=[Message(role="user", content=req.text)],
            user_id=req.user_id,
            agent_id=req.agent_id,
            run_id=req.run_id,
            app_id=req.app_id,
            source=req.source,
            metadata=req.metadata,
            expiration_date=req.expiration_date,
        )
        # Delegate to add() which handles quota check + increment internally
        result = await add(add_req, sub_user_id=sub_user_id, ctx=ctx)
        return result

    def _extract_pdf_with_vision(file_bytes: bytes, filename: str) -> list[str]:
        """Two-pass GPT-5.4 vision extraction from PDF pages."""
        import fitz  # PyMuPDF
        import base64
        from openai import OpenAI
        from concurrent.futures import ThreadPoolExecutor, as_completed

        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            raise ValueError("OPENAI_API_KEY not configured for vision extraction")

        client = OpenAI(api_key=openai_key)

        # Render all pages to PNG at 200 DPI
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_images = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            png_bytes = pix.tobytes("png")
            b64 = base64.b64encode(png_bytes).decode("ascii")
            page_images.append(b64)
        doc.close()

        total_pages = len(page_images)
        logger.info(f"[add_file] PDF rendered: {total_pages} pages from '{filename}'")

        # ---- PASS 1: Document Scan (skip for small docs ≤5 pages) ----
        if total_pages <= 5:
            document_context = f"Document: {filename} ({total_pages} pages)"
            logger.info(f"[add_file] Pass 1 skipped (≤5 pages)")
        else:
            scan_pages = page_images[:3]
            scan_content = [
                {"type": "text", "text": (
                    f"You are analyzing a document: '{filename}' ({total_pages} pages). "
                    "I'm showing you the first few pages. Provide a brief document scan:\n\n"
                    "1. DOCUMENT TYPE: What kind of document is this?\n"
                    "2. PRIMARY TOPIC: Main subject in 1-2 sentences\n"
                    "3. LANGUAGE: What language is the document in?\n"
                    "4. KEY ENTITIES: List the most important people, organizations, "
                    "projects, or concepts mentioned (up to 10)\n"
                    "5. STRUCTURE: How is the document organized?\n\n"
                    "Be concise. This context will guide per-page extraction."
                )},
            ]
            for b64 in scan_pages:
                scan_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"},
                })

            try:
                scan_resp = client.chat.completions.create(
                    model=VISION_MODEL,
                    messages=[{"role": "user", "content": scan_content}],
                    max_completion_tokens=1000,
                )
                document_context = scan_resp.choices[0].message.content or ""
                logger.info(f"[add_file] Pass 1 complete: {len(document_context)} chars context")
            except Exception as e:
                logger.error(f"[add_file] Pass 1 failed, continuing without context: {e}")
                document_context = f"Document: {filename}"

        # ---- PASS 2: Per-Page Extraction (parallel, 5 workers) ----
        def _extract_single_page(page_num: int, b64_image: str) -> tuple:
            page_content = [
                {"type": "text", "text": (
                    f"DOCUMENT CONTEXT:\n{document_context}\n\n---\n\n"
                    f"Extract ALL text and information from page {page_num + 1} of "
                    f"{total_pages} of '{filename}'.\n\n"
                    "INSTRUCTIONS:\n"
                    "- Extract every piece of text visible on the page\n"
                    "- Preserve the logical structure (headings, paragraphs, lists, tables)\n"
                    "- For tables: convert to a readable text format with clear column labels\n"
                    "- For diagrams/charts: describe the data and relationships shown\n"
                    "- For handwritten text: transcribe as accurately as possible\n"
                    "- Include all names, dates, numbers, and specific details\n"
                    "- Preserve any code blocks or technical notation\n"
                    "- Output clean, structured text ready for knowledge extraction\n"
                    "- Do NOT add commentary or interpretation — just extract the content"
                )},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64_image}", "detail": "high"},
                },
            ]
            try:
                resp = client.chat.completions.create(
                    model=VISION_MODEL,
                    messages=[{"role": "user", "content": page_content}],
                    max_completion_tokens=4000,
                )
                text = (resp.choices[0].message.content or "").strip()
                return (page_num, text)
            except Exception as e:
                logger.error(f"[add_file] Page {page_num + 1} extraction failed: {e}")
                return (page_num, "")

        page_texts = [""] * total_pages
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [
                pool.submit(_extract_single_page, i, img)
                for i, img in enumerate(page_images)
            ]
            for future in as_completed(futures):
                page_num, text = future.result()
                page_texts[page_num] = text

        result = [t for t in page_texts if t.strip()]
        logger.info(f"[add_file] Pass 2 complete: {len(result)}/{total_pages} pages extracted")
        return result

    @app.post("/v1/add_file", tags=["Memory"])
    async def add_file(
        file: UploadFile = File(...),
        user_id: str = Form("default"),
        agent_id: str | None = Form(None),
        run_id: str | None = Form(None),
        app_id: str | None = Form(None),
        ctx: AuthContext = Depends(auth),
    ):
        """
        Upload a file (PDF, DOCX, TXT, MD) and extract structured memories.

        PDF files use premium two-pass GPT-5.4 vision extraction.
        Each page/chunk counts as 1 add from your quota.
        Returns immediately with job_id; processes in background.
        """
        import threading

        owner_id = ctx.user_id
        sub_uid = user_id or "default"

        # ---- Validate file type ----
        filename = file.filename or "unknown"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: .{ext}. Supported: PDF, DOCX, TXT, MD.",
            )

        # ---- Read file and check size ----
        file_bytes = await file.read()
        max_size = FILE_SIZE_LIMITS.get(ctx.plan, FILE_SIZE_LIMITS["free"])
        if len(file_bytes) > max_size:
            max_mb = max_size // (1024 * 1024)
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "file_too_large",
                    "size_bytes": len(file_bytes),
                    "limit_bytes": max_size,
                    "limit_mb": max_mb,
                    "plan": ctx.plan,
                    "message": f"File exceeds {max_mb}MB limit for {ctx.plan} plan. "
                               f"Upgrade at {BASE_URL}/#pricing",
                },
            )

        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty file uploaded.")

        # ---- Count pages/chunks (pre-parse text for DOCX/TXT to avoid double parsing) ----
        page_count = 0
        file_type = ext
        pre_parsed_chunks = None  # For DOCX/TXT: reused in background thread

        if file_type == "pdf":
            try:
                import fitz
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                page_count = len(doc)
                doc.close()
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to read PDF: {e}")
            if page_count == 0:
                raise HTTPException(status_code=400, detail="PDF has no pages.")

        elif file_type == "docx":
            try:
                import docx
                import io
                doc = docx.Document(io.BytesIO(file_bytes))
                full_text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to read DOCX: {e}")
            if not full_text.strip():
                raise HTTPException(status_code=400, detail="DOCX has no text content.")
            from importer import chunk_text
            pre_parsed_chunks = chunk_text(full_text, 4000)
            page_count = max(len(pre_parsed_chunks), 1)

        else:  # txt, md
            try:
                full_text = file_bytes.decode("utf-8", errors="replace")
            except Exception:
                full_text = file_bytes.decode("latin-1", errors="replace")
            if not full_text.strip():
                raise HTTPException(status_code=400, detail="File has no text content.")
            from importer import chunk_text
            pre_parsed_chunks = chunk_text(full_text, 4000)
            page_count = max(len(pre_parsed_chunks), 1)

        # ---- Check quota upfront (all pages at once) ----
        use_quota(ctx, "add", count=page_count)

        # ---- Enforce sub-user limit ----
        if sub_uid != "default":
            plan_quotas = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
            max_sub_users = plan_quotas.get("sub_users", 3)
            if max_sub_users != -1:
                distinct_sub_users = store.count_distinct_sub_users(owner_id)
                if distinct_sub_users >= max_sub_users:
                    known = store.is_known_sub_user(owner_id, sub_uid)
                    if not known:
                        raise HTTPException(status_code=402, detail={
                            "error": "quota_exceeded", "action": "sub_users",
                            "limit": max_sub_users, "used": distinct_sub_users,
                            "plan": ctx.plan,
                            "message": f"Sub-user limit reached ({max_sub_users}). "
                                       f"Upgrade your plan.",
                            "upgrade_url": f"{BASE_URL}/#pricing",
                        })

        # ---- Create job and return 202 ----
        job_id = store.create_job(owner_id, "add_file")

        metadata = {"source": "file_upload", "filename": filename,
                     "file_type": file_type, "page_count": page_count}
        if agent_id:
            metadata["agent_id"] = agent_id
        if run_id:
            metadata["run_id"] = run_id
        if app_id:
            metadata["app_id"] = app_id

        def process_file_in_background():
            try:
                # ---- Extract text from file ----
                if file_type == "pdf":
                    page_texts = _extract_pdf_with_vision(file_bytes, filename)
                else:
                    # DOCX/TXT/MD: reuse pre-parsed chunks from validation step
                    page_texts = pre_parsed_chunks or []

                if not page_texts:
                    store.fail_job(job_id, "No text could be extracted from file.")
                    return

                # ---- Convert to conversation and run standard pipeline ----
                # Combine all pages into a single message for faster extraction
                combined_text = ""
                for i, page_text in enumerate(page_texts):
                    label = f"Page {i+1}" if file_type == "pdf" else f"Chunk {i+1}"
                    combined_text += f"--- {label} of {len(page_texts)} ---\n{page_text}\n\n"
                conversation = [{
                    "role": "user",
                    "content": f"Document: {filename}\n\n{combined_text.strip()}",
                }]

                _run_extraction_pipeline(
                    user_id=owner_id,
                    sub_uid=sub_uid,
                    conversation=conversation,
                    metadata=metadata,
                    expiration_date=None,
                    job_id=job_id,
                    plan=ctx.plan,
                )
            except Exception as e:
                logger.error(f"[add_file] Background processing failed: {e}")
                store.fail_job(job_id, str(e))

        threading.Thread(target=process_file_in_background, daemon=True).start()

        from starlette.responses import JSONResponse
        return JSONResponse(status_code=202, content={
            "status": "accepted",
            "message": f"Processing {filename} ({page_count} pages/chunks) in background.",
            "job_id": job_id,
            "file_type": file_type,
            "page_count": page_count,
            "quota_used": page_count,
        })

    @app.get("/v1/jobs/{job_id}", tags=["System"])
    async def job_status(job_id: str, ctx: AuthContext = Depends(auth)):
        """Check status of a background job."""
        user_id = ctx.user_id
        job = store.get_job(job_id, user_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.post("/v1/search", tags=["Search"])
    async def search(req: SearchRequest, sub_user_id: str | None = Query(None), ctx: AuthContext = Depends(auth)):
        """Semantic search across memories with LLM re-ranking."""
        user_id = ctx.user_id
        use_quota(ctx, "search")  # atomic check+increment
        import hashlib as _hashlib

        sub_uid = _resolve_sub_user(req.user_id, sub_user_id)

        # Build metadata filters from explicit fields + filters dict
        meta_filters = dict(req.filters) if req.filters else {}
        if req.agent_id:
            meta_filters["agent_id"] = req.agent_id
        if req.run_id:
            meta_filters["run_id"] = req.run_id
        if req.app_id:
            meta_filters["app_id"] = req.app_id

        # Validate optional threshold (additive — None = server defaults)
        if req.threshold is not None and not (0.0 <= req.threshold <= 1.0):
            raise HTTPException(status_code=400, detail="threshold must be between 0.0 and 1.0")

        # ---- Redis cache: same query → instant response ----
        filter_str = json.dumps(meta_filters, sort_keys=True) if meta_filters else ""
        cache_input = f'{req.query}:{req.limit}:{req.graph_depth}:{req.threshold}:{filter_str}'
        cache_key = f"search:{user_id}:{sub_uid}:{_hashlib.md5(cache_input.encode('utf-8', errors='replace')).hexdigest()}"
        cached = store.cache.get(cache_key)
        if cached:
            top_score = float(cached[0]["score"]) if cached and "score" in cached[0] else 0.0
            store.log_usage(user_id, "search",
                            query_score=top_score,
                            query_language=_detect_query_language(req.query),
                            result_quality=_quality_label(top_score))
            return {"results": cached}

        embedder = get_embedder()

        # Search with more candidates for re-ranking
        search_limit = max(req.limit * 2, 10)

        if embedder:
            try:
                emb = embedder.embed(req.query)
            except Exception as e:
                logger.error(f"Embedding failed: {e}")
                # Fall back to text search if embedding API is unavailable
                results = store.search_text(user_id, req.query, top_k=search_limit, sub_user_id=sub_uid)
                emb = None
            if emb is not None:
                # If client supplied threshold, use it; otherwise let store use its default
                vec_kwargs = dict(top_k=search_limit, query_text=req.query,
                                  graph_depth=req.graph_depth, sub_user_id=sub_uid,
                                  meta_filters=meta_filters)
                if req.threshold is not None:
                    vec_kwargs["min_score"] = req.threshold
                results = store.search_vector_with_teams(user_id, emb, **vec_kwargs)
                # Fallback to looser threshold ONLY when client didn't pin one
                if not results and req.threshold is None:
                    results = store.search_vector_with_teams(user_id, emb, top_k=search_limit,
                                                  min_score=0.2, query_text=req.query,
                                                  graph_depth=req.graph_depth,
                                                  sub_user_id=sub_uid, meta_filters=meta_filters)
        else:
            results = store.search_text(user_id, req.query, top_k=search_limit, sub_user_id=sub_uid)

        # Split direct matches from graph-expanded entities
        direct = [r for r in results if not r.get("_graph")]
        graph = [r for r in results if r.get("_graph")]

        # LLM re-ranking: only rerank direct matches (graph entities are logically relevant)
        if direct and len(direct) > 3:
            direct = rerank_results(req.query, direct, plan=ctx.plan)

        # Merge: direct first, then graph-expanded
        results = direct + graph

        # Limit to requested count
        results = results[:req.limit]

        # Clean up internal flag
        for r in results:
            r.pop("_graph", None)

        # Attach a matching reflection for richer context (word overlap matching).
        # Needs a higher bar than before (0.5 was too loose — single-word overlap
        # dominated short queries and pushed real entities down). Requires:
        #   - at least 3 meaningful words in the query
        #   - overlap >= 0.7
        # Reflection is appended AFTER top entities, not prepended, so factual
        # answers stay on top and the insight acts as optional extra context.
        reflections = store.get_reflections(user_id, sub_user_id=sub_uid)
        if reflections:
            query_words = set(w.lower() for w in req.query.split() if len(w) > 3)
            if len(query_words) >= 3:
                best_match = None
                best_overlap = 0.0
                for r in reflections:
                    ref_text = f"{r['title']} {r['content']}".lower()
                    matching_count = sum(1 for w in query_words if w in ref_text)
                    overlap = matching_count / len(query_words)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_match = r
                if best_match and best_overlap >= 0.7:
                    insight = {
                        "entity": f"✨ Insight: {best_match['title']}",
                        "type": "reflection",
                        "scope": best_match["scope"],
                        "score": best_match["confidence"],
                        "metadata": {},
                        "facts": [best_match["content"]],
                        "relations": [],
                        "knowledge": [],
                    }
                    # Insert after the first 1-2 concrete entities so users see the
                    # direct answer first, then the high-level insight below.
                    insert_at = min(2, len(results))
                    results.insert(insert_at, insight)

        # Cache results in Redis (TTL 30s)
        store.cache.set(cache_key, results, ttl=30)
        # Log usage with retrieval score + detected language for
        # Memory Health monitoring (v2.22, see /v1/health/retrieval).
        top_score = float(results[0]["score"]) if results and "score" in results[0] else 0.0
        store.log_usage(user_id, "search",
                        query_score=top_score,
                        query_language=_detect_query_language(req.query),
                        result_quality=_quality_label(top_score))
        # increment already done atomically in use_quota above

        # Quality label — strong/weak/no_match — so MCP clients and voice
        # adapters can act on retrieval honesty instead of guessing whether
        # a low-score result is "noise" or "best-effort hit." See
        # search_vector floor fix in store.py for the underlying reasoning.
        # Quality label on the score caller sees (post-rerank for Pro+,
        # raw RRF for free/starter). Rerank outputs 0-1 cosine-style scores;
        # raw RRF tops out around 0.05. We use overlapping but distinct
        # bands so callers can decide whether to trust a "weak" result.
        result_quality = _quality_label(top_score)

        response = {
            "results": results,
            "result_quality": result_quality,
            "top_score": round(top_score, 4),
        }
        if not results:
            try:
                st = store.get_stats(user_id, sub_user_id=sub_uid)
                if (st.get("entities", 0) == 0 and st.get("facts", 0) == 0):
                    response["hint"] = (
                        'Your memory is empty — add something first, then search will find it. '
                        'Example: POST /v1/add_text with {"text": "I am a Python developer who uses PostgreSQL"} '
                        'then search for "what database do I use?"'
                    )
                else:
                    response["hint"] = (
                        f"No results matched your query. Try broader terms or different phrasing. "
                        f"Your memory has {st.get('entities', 0)} entities and {st.get('facts', 0)} facts."
                    )
            except Exception:
                response["hint"] = "No memories found. Add your first memory with POST /v1/add — then search will return results."
        return response

    @app.post("/v1/ask", tags=["Search"])
    async def ask(req: AskRequest, sub_user_id: str | None = Query(None), ctx: AuthContext = Depends(auth)):
        """Ask your memory a question — get a synthesized answer with citations.

        RAG flow: embed query → top-N facts via search → Cohere Chat with documents
        → answer text with native source attribution.

        Premium: Pro / Growth / Business only. Counts as 1 search against quota.
        """
        if ctx.plan in ("free", "starter"):
            raise HTTPException(
                status_code=403,
                detail="Ask requires Pro plan. Upgrade at mengram.io/pricing"
            )

        user_id = ctx.user_id
        use_quota(ctx, "search")
        sub_uid = _resolve_sub_user(req.user_id, sub_user_id)

        # 1. Embed query (Cohere multilingual / OpenAI fallback)
        embedder = get_embedder()
        if not embedder:
            raise HTTPException(status_code=503, detail="Embedder not configured")
        try:
            emb = embedder.embed(req.query)
        except Exception as e:
            logger.error(f"Ask: embedding failed: {e}")
            raise HTTPException(status_code=503, detail="Embedding service failed")

        # 2. Retrieve top facts via existing search
        results = store.search_vector_with_teams(
            user_id, emb,
            top_k=max(req.max_facts, 8),
            query_text=req.query,
            sub_user_id=sub_uid,
        )

        if not results:
            return {
                "answer": "I don't have any memories that match your question.",
                "citations": [],
                "facts_used": 0,
            }

        # 3. Format facts as Cohere documents (cap at 30 to control cost)
        documents = []
        fact_map = {}  # doc_id → fact metadata for citation lookup
        MAX_DOCS = 30
        for r in results:
            entity_name = r.get("entity", "")
            for fact in r.get("facts", []):
                if len(documents) >= MAX_DOCS:
                    break
                doc_id = f"f_{len(documents)}"
                # Cohere documents accept dict with string values; combine entity+fact
                # so the model knows what entity each fact belongs to.
                fact_text = fact if isinstance(fact, str) else str(fact)
                documents.append({
                    "id": doc_id,
                    "data": {
                        "entity": entity_name,
                        "fact": fact_text,
                    },
                })
                fact_map[doc_id] = {"entity": entity_name, "fact": fact_text}
            if len(documents) >= MAX_DOCS:
                break

        if not documents:
            return {
                "answer": "I don't have any facts to answer that question yet.",
                "citations": [],
                "facts_used": 0,
            }

        # 4. Call Cohere Chat with documents (RAG with native citations)
        nonlocal _cohere_client
        cohere_key = os.environ.get("COHERE_API_KEY", "")
        if not cohere_key:
            raise HTTPException(status_code=503, detail="Cohere not configured")
        if _cohere_client is None:
            import cohere
            _cohere_client = cohere.ClientV2(api_key=cohere_key)
        co = _cohere_client

        try:
            chat_resp = co.chat(
                model="command-a-03-2025",
                messages=[{"role": "user", "content": req.query}],
                documents=documents,
            )
        except Exception as e:
            logger.error(f"Ask: Cohere chat failed: {e}")
            raise HTTPException(status_code=503, detail="Answer generation failed")

        # 5. Parse Cohere response — concat text blocks, surface citations
        answer_text = ""
        if chat_resp.message and chat_resp.message.content:
            for block in chat_resp.message.content:
                # Cohere v2 returns content as list of blocks; text blocks have .text
                if hasattr(block, "text") and block.text:
                    answer_text += block.text

        citations_out = []
        msg_citations = getattr(chat_resp.message, "citations", None) if chat_resp.message else None
        if msg_citations:
            for cit in msg_citations:
                cited_sources = []
                for src in (cit.sources or []):
                    src_id = getattr(src, "id", None)
                    if src_id and src_id in fact_map:
                        cited_sources.append(fact_map[src_id])
                citations_out.append({
                    "text": cit.text,
                    "start": cit.start,
                    "end": cit.end,
                    "sources": cited_sources,
                })

        store.log_usage(user_id, "ask")
        return {
            "answer": answer_text,
            "citations": citations_out,
            "facts_used": len(documents),
        }

    @app.get("/v1/memories", tags=["Memory"])
    async def get_all(sub_user_id: str = Query("default"),
                      limit: int = Query(100, ge=1, le=500),
                      offset: int = Query(0, ge=0),
                      ctx: AuthContext = Depends(auth)):
        """Get all memories (entities). Supports pagination with limit/offset."""
        user_id = ctx.user_id
        entities, total = store.get_all_entities(user_id, sub_user_id=sub_user_id, limit=limit, offset=offset)
        store.log_usage(user_id, "get_all")
        return {"memories": entities, "total": total, "limit": limit, "offset": offset}

    # How much a single export will serialise. Generous enough that almost
    # nobody meets it, bounded so one request cannot pin a worker.
    EXPORT_CAP = 5000

    def _collect_export(user_id: str, sub_user_id: str) -> dict:
        """Everything the export serialises, read once and reused by both formats."""
        entities = store.get_all_entities_full(user_id, sub_user_id=sub_user_id)[:EXPORT_CAP]
        episodes = store.get_episodes(user_id, limit=EXPORT_CAP, sub_user_id=sub_user_id)
        procedures = store.get_procedures(user_id, limit=EXPORT_CAP, sub_user_id=sub_user_id)

        # A procedure's evolution is the record that earned its trust, so it
        # belongs in the export. One query per procedure, which is why it is
        # skipped once the list gets long.
        evolution = {}
        if len(procedures) <= 200:
            for proc in procedures:
                try:
                    log = store.get_procedure_evolution(user_id, str(proc["id"]),
                                                        sub_user_id=sub_user_id)
                    if log:
                        evolution[str(proc["id"])] = log
                except Exception as e:
                    logger.warning(f"⚠️ Export: evolution for {proc.get('name')}: {e}")

        return {"entities": entities, "episodes": episodes,
                "procedures": procedures, "evolution": evolution}

    @app.get("/v1/export", tags=["Memory"])
    async def export_memory(format: str = Query("markdown", pattern="^(markdown|json|files)$"),
                            sub_user_id: str = Query("default"),
                            ctx: AuthContext = Depends(auth)):
        """Export this memory as plain files you own.

        `format=markdown` returns a zip of an Obsidian-native tree — one file per
        entity, relations as `[[wikilinks]]`, procedures with their track record
        and the failures that changed them. `format=files` returns that same
        tree as `{path: text}` for clients that write files themselves rather
        than unpack an archive — the Obsidian plugin's pull. `format=json`
        returns the underlying records, for callers doing their own thing.

        Read-only, and deliberately not charged against the add or search quota:
        getting your data out should never cost you the ability to use the
        product. The per-minute rate limit still applies.
        """
        from cloud import markdown_export

        user_id = ctx.user_id
        data = _collect_export(user_id, sub_user_id)
        store.log_usage(user_id, "export")

        if format == "json":
            return {
                "entities": data["entities"],
                "episodes": data["episodes"],
                "procedures": data["procedures"],
                "evolution": data["evolution"],
                "counts": {k: len(data[k]) for k in ("entities", "episodes", "procedures")},
            }

        # Everything below builds the Markdown tree; markdown zips it, files
        # hands it over as-is. One serialiser, so the zip a person downloads
        # and the files a plugin writes can never say different things.
        profile = None
        try:
            # Cached; never generated on the export path, so an export cannot
            # trigger a model call the caller did not ask for.
            cached = store.cache.get(f"profile:{user_id}")
            if isinstance(cached, dict):
                profile = cached.get("profile")
        except Exception:
            pass

        tree = markdown_export.build_tree(
            entities=data["entities"], episodes=data["episodes"],
            procedures=data["procedures"], profile=profile,
            evolution_by_procedure=data["evolution"],
        )

        if format == "files":
            return {"files": tree, "counts": {
                k: len(data[k]) for k in ("entities", "episodes", "procedures")}}

        import io as _io
        import zipfile as _zipfile
        buffer = _io.BytesIO()
        with _zipfile.ZipFile(buffer, "w", _zipfile.ZIP_DEFLATED) as archive:
            for path, text in sorted(tree.items()):
                archive.writestr(path, text)
        buffer.seek(0)

        stamp = datetime.date.today().isoformat()
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="mengram-{stamp}.zip"'},
        )

    @app.post("/v1/reindex", tags=["Memory"])
    async def reindex(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Re-generate all embeddings (includes relations now)."""
        user_id = ctx.user_id
        embedder = get_embedder()
        if not embedder:
            raise HTTPException(status_code=500, detail="No embedder configured")

        # Count entities first, use_quota with actual count
        entities = store.get_all_entities_full(user_id, sub_user_id=sub_user_id)
        use_quota(ctx, "reindex")  # atomic check+increment
        count = 0
        for entity in entities:
            name = entity["entity"]
            entity_id = store.get_entity_id(user_id, name, sub_user_id=sub_user_id)
            if not entity_id:
                continue

            chunks = store.build_entity_chunks(
                entity_id, name, summarize=_summarize_for_embedding)
            embeddings = embedder.embed_batch(chunks)
            if len(embeddings) != len(chunks):
                logger.error(f"⚠️ Reindex skipped '{name}': embedder returned "
                             f"{len(embeddings)} vectors for {len(chunks)} chunks")
                continue
            store.delete_embeddings(entity_id)
            for chunk, emb in zip(chunks, embeddings):
                store.save_embedding(entity_id, chunk, emb)
            count += 1

        # increment already done in use_quota above
        return {"reindexed": count}

    @app.post("/v1/dedup", tags=["Memory"])
    async def dedup(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Find and merge duplicate entities."""
        user_id = ctx.user_id
        use_quota(ctx, "dedup")  # atomic check+increment
        entities = store.get_all_entities(user_id, sub_user_id=sub_user_id)
        names = [(e["name"], e.get("type", "unknown")) for e in entities]
        merged = []

        # Compare all pairs — find word-boundary matches (e.g. "Ali" + "Ali Baizhanov")
        processed = set()
        for i, (name_a, _) in enumerate(names):
            if name_a in processed:
                continue
            for j, (name_b, _) in enumerate(names):
                if i >= j or name_b in processed:
                    continue
                a_lower = name_a.strip().lower()
                b_lower = name_b.strip().lower()
                # One must start with the other + space, or be equal
                is_match = (
                    b_lower.startswith(a_lower + " ") or
                    a_lower.startswith(b_lower + " ") or
                    a_lower == b_lower
                )
                if is_match:
                    # Merge shorter into longer
                    canonical = name_a if len(name_a) >= len(name_b) else name_b
                    shorter = name_b if canonical == name_a else name_a
                    canon_id = store.get_entity_id(user_id, canonical, sub_user_id=sub_user_id)
                    short_id = store.get_entity_id(user_id, shorter, sub_user_id=sub_user_id)
                    if canon_id and short_id and canon_id != short_id:
                        store.merge_entities(user_id, short_id, canon_id, canonical)
                        merged.append(f"{shorter} → {canonical}")
                        processed.add(shorter)

        # increment already done in use_quota above
        return {"merged": merged, "count": len(merged)}

    @app.delete("/v1/entity/{name}", tags=["Memory"])
    async def delete_entity(name: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Delete an entity and all its facts, relations, knowledge, embeddings."""
        user_id = ctx.user_id
        entity_id = store.get_entity_id(user_id, name, sub_user_id=sub_user_id)
        if not entity_id:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")
        with store._cursor() as cur:
            cur.execute("DELETE FROM embeddings WHERE entity_id = %s", (entity_id,))
            cur.execute("DELETE FROM knowledge WHERE entity_id = %s", (entity_id,))
            cur.execute("DELETE FROM facts WHERE entity_id = %s", (entity_id,))
            cur.execute("DELETE FROM relations WHERE source_id = %s OR target_id = %s", (entity_id, entity_id))
            cur.execute("DELETE FROM entities WHERE id = %s", (entity_id,))
        store.fire_webhooks(user_id, "memory_delete", {"entity": name})
        return {"deleted": name}

    @app.post("/v1/identity", tags=["Memory"])
    async def set_identity(entity: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Pin which entity is YOU. Extraction, 'User' merging and profile generation
        anchor to the pinned entity instead of guessing by name/fact-count heuristics.
        Fixes identity drift when third parties are frequently co-mentioned (issue #54)."""
        user_id = ctx.user_id
        result = store.set_user_identity(user_id, entity, sub_user_id=sub_user_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Entity '{entity}' not found")
        return {"status": "pinned", **result}

    @app.post("/v1/merge_user", tags=["Memory"])
    async def merge_user_entity(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Merge 'User' entity into the primary person entity (e.g. 'Ali Baizhanov')."""
        user_id = ctx.user_id
        user_entity_id = store.get_entity_id(user_id, "User", sub_user_id=sub_user_id)
        if not user_entity_id:
            return {"status": "skip", "message": "No 'User' entity found"}

        primary = store._find_primary_person(user_id, sub_user_id=sub_user_id)
        if not primary:
            return {"status": "skip", "message": "No primary person entity to merge into"}

        target_id, target_name = primary
        if user_entity_id == target_id:
            return {"status": "skip", "message": "User IS the primary entity"}

        store.merge_entities(user_id, user_entity_id, target_id, target_name)
        return {"status": "merged", "from": "User", "into": target_name, "target_id": target_id}

    @app.post("/v1/merge", tags=["Memory"])
    async def merge_entities_endpoint(source: str, target: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Merge source entity into target. Source gets deleted, all data moves to target."""
        user_id = ctx.user_id
        source_id = store.get_entity_id(user_id, source, sub_user_id=sub_user_id)
        if not source_id:
            raise HTTPException(status_code=404, detail=f"Source entity '{source}' not found")
        target_id = store.get_entity_id(user_id, target, sub_user_id=sub_user_id)
        if not target_id:
            raise HTTPException(status_code=404, detail=f"Target entity '{target}' not found")
        if source_id == target_id:
            return {"status": "skip", "message": "Same entity"}
        store.merge_entities(user_id, source_id, target_id, target)
        return {"status": "merged", "from": source, "into": target}

    @app.patch("/v1/entity/{name}/type")
    async def fix_entity_type(name: str, new_type: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Fix entity type (e.g. 'company' → 'technology')."""
        user_id = ctx.user_id
        new_type = new_type.strip().lower()
        if not new_type or len(new_type) > 50:
            raise HTTPException(status_code=400, detail="Type must be a non-empty string (max 50 chars)")
        entity_id = store.get_entity_id(user_id, name, sub_user_id=sub_user_id)
        if not entity_id:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")
        with store._cursor() as cur:
            cur.execute("UPDATE entities SET type = %s WHERE id = %s", (new_type, entity_id))
        return {"entity": name, "new_type": new_type}

    @app.post("/v1/entity/{name}/dedup", tags=["Memory"])
    async def dedup_entity(name: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Use LLM to deduplicate facts on an entity. Keeps best version, archives redundant ones."""
        user_id = ctx.user_id
        use_quota(ctx, "dedup")  # atomic check+increment
        entity_id = store.get_entity_id(user_id, name, sub_user_id=sub_user_id)
        if not entity_id:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")
        extractor = get_llm()
        result = store.dedup_entity_facts(entity_id, name, extractor.llm)
        return result

    @app.post("/v1/dedup_all", tags=["Memory"])
    async def dedup_all_entities(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Deduplicate facts across ALL entities for this user."""
        user_id = ctx.user_id
        use_quota(ctx, "dedup")  # atomic check+increment
        entities = store.get_all_entities(user_id, sub_user_id=sub_user_id)
        extractor = get_llm()
        total_archived = 0
        results = []
        for e in entities:
            entity_id = store.get_entity_id(user_id, e["name"], sub_user_id=sub_user_id)
            if not entity_id:
                continue
            r = store.dedup_entity_facts(entity_id, e["name"], extractor.llm)
            if r["archived"]:
                total_archived += len(r["archived"])
                results.append({"entity": e["name"], "archived": len(r["archived"])})
        return {"total_archived": total_archived, "entities": results}

    # ---- Reflection ----

    @app.post("/v1/reflect", tags=["Insights"])
    async def trigger_reflection(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Manually trigger memory reflection. Generates AI insights from facts."""
        user_id = ctx.user_id
        use_quota(ctx, "reflect")  # atomic check+increment
        extractor = get_llm()
        stats = store.get_reflection_stats(user_id, sub_user_id=sub_user_id)
        result = store.generate_reflections(user_id, extractor.llm, sub_user_id=sub_user_id)

        entity_count = len(result.get("entity_reflections", []))
        cross_count = len(result.get("cross_entity", []))
        temporal_count = len(result.get("temporal", []))
        return {
            "status": "reflected",
            "generated": {
                "entity_reflections": entity_count,
                "cross_entity": cross_count,
                "temporal": temporal_count,
            },
            "stats_before": stats,
        }

    @app.get("/v1/reflections", tags=["Insights"])
    async def get_reflections(scope: str = None, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Get all reflections. Optional ?scope=entity|cross|temporal. Each item includes its id (deletable via DELETE /v1/reflections/{id})."""
        user_id = ctx.user_id
        return {"reflections": store.get_reflections(user_id, scope=scope, sub_user_id=sub_user_id)}

    @app.delete("/v1/reflections/{reflection_id}", tags=["Insights"])
    async def delete_reflection(reflection_id: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Delete a single reflection by id. Use when a generated reflection is
        wrong or polluted (e.g. cross-entity identity mixups) — the next
        reflection pass will regenerate from clean facts."""
        user_id = ctx.user_id
        deleted = store.delete_reflection(user_id, reflection_id, sub_user_id=sub_user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Reflection '{reflection_id}' not found")
        return {"status": "deleted", "reflection_id": reflection_id}

    @app.get("/v1/insights", tags=["Insights"])
    async def get_insights(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Get formatted AI insights for dashboard."""
        user_id = ctx.user_id
        return store.get_insights(user_id, sub_user_id=sub_user_id)

    # =====================================================
    # MEMORY AGENTS v2.0
    # =====================================================

    @app.post("/v1/agents/run", tags=["Agents"])
    async def run_agents(
        agent: str = "all",
        auto_fix: bool = False,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """Run memory agents.
        ?agent=curator|connector|digest|all
        ?auto_fix=true — auto-archive low quality and stale facts (curator only)
        Returns a job_id immediately; agents run in the background.
        """
        user_id = ctx.user_id
        use_quota(ctx, "agent")  # atomic check+increment

        if agent not in ("all", "curator", "connector", "digest"):
            raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}. Use: curator, connector, digest, reclassify, all")

        job_id = store.create_job(user_id, f"agents_{agent}")

        def run_agents_background():
            try:
                llm = get_llm()
                if agent == "all":
                    result = store.run_all_agents(user_id, llm.llm, auto_fix=auto_fix, sub_user_id=sub_user_id)
                    store.complete_job(job_id, {"agents": result})
                elif agent == "curator":
                    result = store.run_curator_agent(user_id, llm.llm, auto_fix=auto_fix, sub_user_id=sub_user_id)
                    store.complete_job(job_id, {"agent": "curator", "result": result})
                elif agent == "connector":
                    result = store.run_connector_agent(user_id, llm.llm, sub_user_id=sub_user_id)
                    store.complete_job(job_id, {"agent": "connector", "result": result})
                elif agent == "digest":
                    result = store.run_digest_agent(user_id, llm.llm, sub_user_id=sub_user_id)
                    store.complete_job(job_id, {"agent": "digest", "result": result})
                elif agent == "reclassify":
                    result = store.reclassify_unknown_entities(user_id, llm.llm, sub_user_id=sub_user_id)
                    store.complete_job(job_id, {"agent": "reclassify", "result": result})
                logger.info(f"✅ Agents ({agent}) completed for {user_id}")
            except Exception as e:
                logger.error(f"❌ Agents ({agent}) failed for {user_id}: {e}")
                store.fail_job(job_id, str(e))

        threading.Thread(target=run_agents_background, daemon=True).start()

        from starlette.responses import JSONResponse
        return JSONResponse(status_code=202, content={
            "status": "accepted",
            "message": f"Agent(s) '{agent}' running in background.",
            "job_id": job_id,
        })

    @app.get("/v1/agents/history", tags=["Agents"])
    async def agent_history(
        agent: str = None,
        limit: int = 10,
        ctx: AuthContext = Depends(auth)
    ):
        """Get agent run history. Optional ?agent=curator|connector|digest"""
        user_id = ctx.user_id
        runs = store.get_agent_history(user_id, agent_type=agent, limit=limit)
        return {"runs": runs, "total": len(runs)}

    @app.get("/v1/agents/status", tags=["Agents"])
    async def agent_status(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Check which agents are due to run."""
        user_id = ctx.user_id
        due = store.should_run_agents(user_id, sub_user_id=sub_user_id)
        history = store.get_agent_history(user_id, limit=3)
        return {
            "due": due,
            "last_runs": history
        }

    # =====================================================
    # WEBHOOKS
    # =====================================================

    @app.post("/v1/webhooks", tags=["Webhooks"])
    async def create_webhook(req: dict, ctx: AuthContext = Depends(auth)):
        """Create a webhook.
        Body: {"url": "https://...", "name": "My Hook", "event_types": ["memory_add"], "secret": "optional"}
        """
        user_id = ctx.user_id
        url = req.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="url is required")

        # Validate webhook URL (prevent SSRF to internal networks)
        if _is_private_url(url):
            raise HTTPException(status_code=400, detail="Internal/private URLs are not allowed")

        # Enforce webhook count limit per plan
        plan_quotas = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
        max_webhooks = plan_quotas.get("webhooks", 0)
        if max_webhooks != -1:
            existing = store.get_webhooks(user_id)
            if len(existing) >= max_webhooks:
                raise HTTPException(status_code=402, detail={
                    "error": "quota_exceeded", "action": "webhooks",
                    "limit": max_webhooks, "used": len(existing), "plan": ctx.plan,
                    "message": f"Webhook limit reached ({max_webhooks}). Upgrade your plan.",
                    "upgrade_url": f"{BASE_URL}/#pricing",
                })

        try:
            hook = store.create_webhook(
                user_id=user_id,
                url=url,
                name=req.get("name", ""),
                event_types=req.get("event_types"),
                secret=req.get("secret", "")
            )
            return {"status": "created", "webhook": hook}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/v1/webhooks", tags=["Webhooks"])
    async def list_webhooks(ctx: AuthContext = Depends(auth)):
        """List all webhooks."""
        user_id = ctx.user_id
        hooks = store.get_webhooks(user_id)
        return {"webhooks": hooks, "total": len(hooks)}

    @app.put("/v1/webhooks/{webhook_id}", tags=["Webhooks"])
    async def update_webhook(webhook_id: int, req: dict, ctx: AuthContext = Depends(auth)):
        """Update a webhook. Body: any of {url, name, event_types, active}"""
        user_id = ctx.user_id
        # SSRF check on URL update
        new_url = req.get("url")
        if new_url and _is_private_url(new_url):
            raise HTTPException(status_code=400, detail="Internal/private URLs are not allowed")
        result = store.update_webhook(
            user_id=user_id,
            webhook_id=webhook_id,
            url=req.get("url"),
            name=req.get("name"),
            event_types=req.get("event_types"),
            active=req.get("active")
        )
        return result

    @app.delete("/v1/webhooks/{webhook_id}", tags=["Webhooks"])
    async def delete_webhook(webhook_id: int, ctx: AuthContext = Depends(auth)):
        """Delete a webhook."""
        user_id = ctx.user_id
        deleted = store.delete_webhook(user_id, webhook_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Webhook not found")
        return {"status": "deleted", "id": webhook_id}

    # =====================================================
    # TEAMS — SHARED MEMORY
    # =====================================================

    @app.post("/v1/teams", tags=["Teams"])
    async def create_team(req: dict, ctx: AuthContext = Depends(auth)):
        """Create a team. Body: {"name": "My Team", "description": "optional"}"""
        user_id = ctx.user_id
        name = req.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="name is required")

        # Enforce team count limit per plan
        plan_quotas = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
        max_teams = plan_quotas.get("teams", 0)
        if max_teams != -1:
            existing = store.get_user_teams(user_id)
            owned = [t for t in existing if t.get("role") == "owner"]
            if len(owned) >= max_teams:
                raise HTTPException(status_code=402, detail={
                    "error": "quota_exceeded", "action": "teams",
                    "limit": max_teams, "used": len(owned), "plan": ctx.plan,
                    "message": f"Team limit reached ({max_teams}). Upgrade your plan.",
                    "upgrade_url": f"{BASE_URL}/#pricing",
                })

        team = store.create_team(user_id, name, req.get("description", ""))
        return {"status": "created", "team": team}

    @app.get("/v1/teams", tags=["Teams"])
    async def list_teams(ctx: AuthContext = Depends(auth)):
        """List user's teams."""
        user_id = ctx.user_id
        teams = store.get_user_teams(user_id)
        return {"teams": teams, "total": len(teams)}

    @app.post("/v1/teams/join", tags=["Teams"])
    async def join_team(req: dict, ctx: AuthContext = Depends(auth)):
        """Join a team. Body: {"invite_code": "abc123"}"""
        user_id = ctx.user_id
        code = req.get("invite_code")
        if not code:
            raise HTTPException(status_code=400, detail="invite_code is required")
        try:
            result = store.join_team(user_id, code)
            return {"status": "joined", **result}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/v1/teams/{team_id}/members", tags=["Teams"])
    async def team_members(team_id: int, ctx: AuthContext = Depends(auth)):
        """Get team members."""
        user_id = ctx.user_id
        try:
            members = store.get_team_members(user_id, team_id)
            return {"members": members, "total": len(members)}
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

    @app.post("/v1/teams/{team_id}/share", tags=["Teams"])
    async def share_entity(team_id: int, req: dict, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Share a memory with team. Body: {"entity": "Redis"}"""
        user_id = ctx.user_id
        entity_name = req.get("entity")
        if not entity_name:
            raise HTTPException(status_code=400, detail="entity name is required")
        try:
            return store.share_entity(user_id, entity_name, team_id, sub_user_id=sub_user_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/v1/teams/{team_id}/unshare", tags=["Teams"])
    async def unshare_entity(team_id: int, req: dict, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Make a shared memory personal again. Body: {"entity": "Redis"}"""
        user_id = ctx.user_id
        entity_name = req.get("entity")
        if not entity_name:
            raise HTTPException(status_code=400, detail="entity name is required")
        return store.unshare_entity(user_id, entity_name, sub_user_id=sub_user_id)

    @app.post("/v1/teams/{team_id}/leave", tags=["Teams"])
    async def leave_team(team_id: int, ctx: AuthContext = Depends(auth)):
        """Leave a team."""
        user_id = ctx.user_id
        if store.leave_team(user_id, team_id):
            return {"status": "left"}
        raise HTTPException(status_code=400, detail="Cannot leave (owner or not a member)")

    @app.delete("/v1/teams/{team_id}", tags=["Teams"])
    async def delete_team(team_id: int, ctx: AuthContext = Depends(auth)):
        """Delete a team (owner only)."""
        user_id = ctx.user_id
        try:
            store.delete_team(user_id, team_id)
            return {"status": "deleted"}
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

    @app.post("/v1/archive_fact", tags=["Memory"])
    async def archive_fact(
        req: dict,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """Manually archive a wrong fact."""
        user_id = ctx.user_id
        entity_name = req.get("entity_name")
        fact = req.get("fact_content") or req.get("fact")
        if not entity_name or not fact:
            raise HTTPException(status_code=400, detail="entity_name and fact_content required")
        entity_id = store.get_entity_id(user_id, entity_name, sub_user_id=sub_user_id)
        if not entity_id:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")
        with store._cursor() as cur:
            cur.execute(
                """UPDATE facts SET archived = TRUE, superseded_by = 'manually archived'
                   WHERE entity_id = %s AND content = %s AND archived = FALSE""",
                (entity_id, fact)
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Fact not found")
        store._schedule_matview_refresh()
        return {"archived": fact, "entity": entity_name}

    @app.get("/v1/timeline", tags=["Memory"])
    async def timeline(
        after: str = None, before: str = None,
        limit: int = 20,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """Temporal search — what happened in a time range?
        after/before: ISO datetime strings (e.g. 2025-02-01T00:00:00Z)"""
        user_id = ctx.user_id
        results = store.search_temporal(user_id, after=after, before=before, top_k=limit, sub_user_id=sub_user_id)
        return {"results": results}

    @app.get("/v1/memories/full", tags=["Memory"])
    async def get_all_full(sub_user_id: str = Query("default"),
                           limit: int = Query(100, ge=1, le=500),
                           offset: int = Query(0, ge=0),
                           ctx: AuthContext = Depends(auth)):
        """Get all memories with full facts, relations, knowledge. Supports pagination."""
        user_id = ctx.user_id
        entities = store.get_all_entities_full(user_id, sub_user_id=sub_user_id)
        total = len(entities)
        entities = entities[offset:offset + limit]
        store.log_usage(user_id, "get_all")
        return {"memories": entities, "total": total, "limit": limit, "offset": offset}

    @app.get("/v1/memory/{name}", tags=["Memory"])
    async def get_memory(name: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Get specific entity details."""
        user_id = ctx.user_id
        entity = store.get_entity(user_id, name, sub_user_id=sub_user_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")
        return {
            "entity": entity.name,
            "type": entity.type,
            "facts": entity.facts,
            "relations": entity.relations,
            "knowledge": entity.knowledge,
            "metadata": entity.metadata or {},
        }

    @app.delete("/v1/memory/{name}", tags=["Memory"])
    async def delete_memory(name: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Delete a memory."""
        user_id = ctx.user_id
        deleted = store.delete_entity(user_id, name, sub_user_id=sub_user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")
        return {"status": "deleted", "entity": name}

    @app.delete("/v1/memories/all", tags=["Memory"])
    async def delete_all_memories(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Delete ALL memories for this sub-user — entities and their facts,
        plus episodes, procedures, raw conversation text and triggers.
        Irreversible."""
        user_id = ctx.user_id
        counts = store.delete_all_memories(user_id, sub_user_id=sub_user_id)
        total = sum(counts.values())
        logger.warning(
            f"🗑️ DELETE ALL | user={user_id[:8]} | sub={sub_user_id} | rows={total} | {counts}")
        # `count` stays the entity count the dashboard has always shown.
        return {"status": "deleted", "count": counts.get("entities", 0),
                "deleted": counts, "rows": total}

    @app.get("/v1/capture-policy", tags=["System"])
    async def get_capture_policy(ctx: AuthContext = Depends(auth)):
        """Get the account's capture boundary — what extraction is allowed to
        persist. Empty = capture everything (default). Deterministic,
        server-side: category packs + custom keywords + source rules."""
        policy = store.get_capture_policy(ctx.user_id)
        return {
            "capture_policy": policy,
            "available_categories": list(store.CAPTURE_CATEGORY_PACKS.keys()),
        }

    class CapturePolicyRequest(BaseModel):
        deny_categories: list[str] | None = None   # subset of available_categories
        deny_keywords: list[str] | None = None      # custom words/phrases to never store
        deny_sources: list[str] | None = None       # skip adds from these sources
        allow_sources: list[str] | None = None      # if set, ONLY accept these sources

    @app.put("/v1/capture-policy", tags=["System"])
    async def set_capture_policy(req: CapturePolicyRequest, ctx: AuthContext = Depends(auth)):
        """Set the capture boundary. Applied deterministically before any
        extracted memory is persisted — facts, episodes, and procedures
        matching a deny rule are dropped, never written. Enforced server-side,
        not a prompt asking the model to behave."""
        valid = set(store.CAPTURE_CATEGORY_PACKS.keys())
        bad = [c for c in (req.deny_categories or []) if c not in valid]
        if bad:
            raise HTTPException(status_code=400,
                                detail=f"Unknown categories: {bad}. Valid: {sorted(valid)}")
        policy = {k: v for k, v in {
            "deny_categories": req.deny_categories or [],
            "deny_keywords": req.deny_keywords or [],
            "deny_sources": req.deny_sources or [],
            "allow_sources": req.allow_sources or [],
        }.items() if v}
        saved = store.set_capture_policy(ctx.user_id, policy)
        return {"status": "saved", "capture_policy": saved}

    @app.delete("/v1/account", tags=["System"])
    async def delete_account(confirm: str = Query(""), ctx: AuthContext = Depends(auth)):
        """Permanently delete this account and ALL associated data (memories,
        episodes, procedures, chunks, webhooks, teams you created, API keys,
        usage history). Irreversible. Requires confirm=<your account email>.
        An active paid subscription is canceled in Paddle first — if that
        cancellation fails, deletion aborts so you don't keep being billed."""
        user_id = ctx.user_id
        email = store.get_user_email(user_id) or ""
        if not confirm or confirm.strip().lower() != email.strip().lower():
            raise HTTPException(
                status_code=400,
                detail="Pass confirm=<your account email> to delete this account. This cannot be undone."
            )

        sub = store.get_subscription(user_id) or {}
        paddle_sub_id = sub.get("paddle_subscription_id")
        if paddle_sub_id and sub.get("status") in ("active", "past_due"):
            if not PADDLE_API_KEY:
                raise HTTPException(
                    status_code=409,
                    detail="Active subscription found but billing is not configured on this server. "
                           "Cancel the subscription first, then retry."
                )
            try:
                _paddle_request("POST", f"/subscriptions/{paddle_sub_id}/cancel",
                                {"effective_from": "immediately"})
                logger.info(f"Subscription {paddle_sub_id} canceled for account deletion | user={user_id[:8]}")
            except Exception as e:
                logger.error(f"Paddle cancel failed during account deletion | user={user_id[:8]} | {e}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Could not cancel your subscription ({e}). "
                           "Cancel it via the billing portal first, then retry account deletion."
                )

        counts = store.delete_account(user_id)
        logger.warning(f"🗑️ ACCOUNT DELETED | user={user_id[:8]} | email={email} | {counts}")
        return {"status": "deleted", "account": email, "deleted": counts}

    @app.get("/v1/stats", tags=["System"])
    async def stats(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Usage statistics."""
        user_id = ctx.user_id
        return store.get_stats(user_id, sub_user_id=sub_user_id)

    @app.get("/v1/stats/weekly", tags=["System"])
    async def stats_weekly(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Weekly memory report: facts/procedures learned, recalls served,
        repeated-mistake preventions."""
        return store.weekly_stats(ctx.user_id, sub_user_id=sub_user_id)

    @app.get("/v1/intelligence", tags=["System"])
    async def intelligence(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Intelligence dashboard — compound learning metrics."""
        return store.get_intelligence_dashboard(ctx.user_id, sub_user_id=sub_user_id)

    @app.get("/v1/graph", tags=["Memory"])
    async def graph(sub_user_id: str = Query("default"),
                    limit: int = Query(150, ge=1, le=500),
                    ctx: AuthContext = Depends(auth)):
        """Knowledge graph for visualization. Returns top N nodes by connections."""
        user_id = ctx.user_id
        return store.get_graph(user_id, sub_user_id=sub_user_id, limit=limit)

    @app.get("/v1/feed", tags=["Memory"])
    async def feed(limit: int = 50, offset: int = Query(0, ge=0),
                   sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Memory feed — recent facts with timestamps for dashboard."""
        user_id = ctx.user_id
        return store.get_feed(user_id, limit=min(limit, 100), offset=offset, sub_user_id=sub_user_id)

    @app.get("/v1/profile/{target_user_id}", tags=["Memory"])
    async def get_profile(target_user_id: str, force: bool = False, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Cognitive Profile — generates a ready-to-use system prompt from user memory.

        Returns a personalization prompt that can be inserted into any LLM.
        Cached for 1 hour. Use force=true to regenerate (Pro+ only)."""
        user_id = ctx.user_id
        if target_user_id != user_id:
            raise HTTPException(status_code=403, detail="Cannot access another user's profile")
        # force=true bypasses cache → LLM call, restrict to paid plans
        if force and ctx.plan in ("free", "starter"):
            force = False
        # Meter "rules" only for a real LLM (re)generation; cached/empty reads are free.
        was_cached = (not force) and store.cache.get(f"profile:{target_user_id}:{sub_user_id}") is not None
        result = store.get_profile(target_user_id, force=force, sub_user_id=sub_user_id)
        if not was_cached and result.get("status") == "ok":
            use_quota(ctx, "rules")
        return result

    @app.get("/v1/profile", tags=["Memory"])
    async def get_own_profile(force: bool = False, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Cognitive Profile for the authenticated user."""
        user_id = ctx.user_id
        if force and ctx.plan in ("free", "starter"):
            force = False
        # Meter "rules" only for a real LLM (re)generation. A cached profile or an
        # empty-memory profile is a free read — important because the MCP connector
        # fetches this per request while building server instructions, which used
        # to exhaust the small free "rules" quota on the first connect.
        was_cached = (not force) and store.cache.get(f"profile:{user_id}:{sub_user_id}") is not None
        result = store.get_profile(user_id, force=force, sub_user_id=sub_user_id)
        if not was_cached and result.get("status") == "ok":
            use_quota(ctx, "rules")
        return result

    @app.get("/v1/rules", tags=["Memory"])
    async def generate_rules(
        format: str = Query("claude_md"),
        force: bool = False,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth),
    ):
        """Generate a CLAUDE.md, .cursorrules, or .windsurfrules file from memory.
        Returns structured project rules and conventions extracted from all memory types."""
        if format not in ("claude_md", "cursorrules", "windsurf"):
            format = "claude_md"
        user_id = ctx.user_id
        use_quota(ctx, "rules")
        if force and ctx.plan in ("free", "starter"):
            force = False
        if force:
            store.cache.invalidate(f"rules:{user_id}:{sub_user_id}:{format}")
        return store.generate_rules_file(user_id, format=format, sub_user_id=sub_user_id)

    # ---- Episodic Memory ----

    @app.get("/v1/episodes", tags=["Episodic Memory"])
    async def list_episodes(
        limit: int = Query(20, ge=1, le=500), offset: int = Query(0, ge=0),
        after: str = None, before: str = None,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """List episodic memories (events, interactions, experiences). Supports pagination."""
        user_id = ctx.user_id
        episodes = store.get_episodes(user_id, limit=limit, offset=offset,
                                       after=after, before=before, sub_user_id=sub_user_id)
        total = store.count_episodes(user_id, after=after, before=before, sub_user_id=sub_user_id)
        return {"episodes": episodes, "count": len(episodes),
                "total": total, "limit": limit, "offset": offset}

    @app.get("/v1/episodes/search", tags=["Episodic Memory"])
    async def search_episodes(
        query: str, limit: int = 5,
        after: str = None, before: str = None,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """Semantic search over episodic memories."""
        user_id = ctx.user_id
        use_quota(ctx, "search")  # counts as a search operation (embedding call)
        embedder = get_embedder()
        if embedder:
            emb = embedder.embed(query)
            results = store.search_episodes_vector(
                user_id, emb, top_k=limit, after=after, before=before, sub_user_id=sub_user_id, query_text=query)
        else:
            results = store.search_episodes_text(user_id, query, top_k=limit, sub_user_id=sub_user_id)
        return {"results": results}

    # ---- Procedural Memory ----

    @app.get("/v1/procedures", tags=["Procedural Memory"])
    async def list_procedures(
        limit: int = Query(20, ge=1, le=500), offset: int = Query(0, ge=0),
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """List procedural memories (learned workflows, skills). Supports pagination."""
        user_id = ctx.user_id
        procedures = store.get_procedures(user_id, limit=limit, offset=offset, sub_user_id=sub_user_id)
        total = store.count_procedures(user_id, sub_user_id=sub_user_id)
        return {"procedures": procedures, "count": len(procedures),
                "total": total, "limit": limit, "offset": offset}

    @app.get("/v1/procedures/search", tags=["Procedural Memory"])
    async def search_procedures(
        query: str, limit: int = 5,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """Semantic search over procedural memories."""
        user_id = ctx.user_id
        use_quota(ctx, "search")  # counts as a search operation (embedding call)
        embedder = get_embedder()
        if embedder:
            emb = embedder.embed(query)
            results = store.search_procedures_vector(user_id, emb, top_k=limit, sub_user_id=sub_user_id, query_text=query)
        else:
            results = store.search_procedures_text(user_id, query, top_k=limit, sub_user_id=sub_user_id)
        return {"results": results}

    @app.patch("/v1/procedures/{procedure_id}/feedback", tags=["Procedural Memory"])
    async def procedure_feedback(
        procedure_id: str, success: bool = True,
        body: FeedbackRequest = None,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """Record success/failure feedback for a procedure.

        On failure with context, triggers experience-driven evolution:
        creates a linked failure episode and evolves the procedure to a new version.
        """
        _require_full_uuid(procedure_id, "procedure_id")
        user_id = ctx.user_id
        # Evolution on failure is Pro only
        if not success and body and body.context:
            if ctx.plan in ("free", "starter"):
                raise HTTPException(status_code=403, detail="Procedure evolution is a Pro feature. Upgrade at mengram.io/dashboard")
            use_quota(ctx, "add")
        result = store.procedure_feedback(user_id, procedure_id, success,
                                          sub_user_id=sub_user_id,
                                          failed_at_step=body.failed_at_step if body else None)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])

        # Experience-driven evolution: on failure with context, evolve the procedure
        evolution_triggered = False
        if not success and body and body.context:
            import threading

            def evolve_in_background():
                try:
                    # 1. Create a linked failure episode
                    episode_id = store.save_episode(
                        user_id=user_id,
                        summary=f"Procedure '{result['name']}' failed: {body.context[:100]}",
                        context=body.context,
                        outcome="failure",
                        emotional_valence="negative",
                        importance=0.7,
                        linked_procedure_id=procedure_id,
                        failed_at_step=body.failed_at_step,
                        sub_user_id=sub_user_id,
                    )
                    # Embed the failure episode
                    embedder = get_embedder()
                    if embedder:
                        ep_text = f"Procedure {result['name']} failed. {body.context}"[:2000]
                        ep_embs = embedder.embed_batch([ep_text])
                        if ep_embs:
                            store.save_episode_embedding(episode_id, ep_text, ep_embs[0])

                    # 2. Trigger evolution
                    from cloud.evolution import EvolutionEngine
                    extractor = get_llm()
                    engine = EvolutionEngine(store, embedder, extractor.llm)
                    engine.evolve_on_failure(user_id, procedure_id, episode_id, body.context, sub_user_id=sub_user_id)
                except Exception as e:
                    logger.error(f"⚠️ Procedure evolution failed: {e}")

            threading.Thread(target=evolve_in_background, daemon=True).start()
            evolution_triggered = True

        result["evolution_triggered"] = evolution_triggered
        return result

    @app.get("/v1/procedures/{procedure_id}/history", tags=["Procedural Memory"])
    async def procedure_history(procedure_id: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Get version history for a procedure. Shows how it evolved over time."""
        _require_full_uuid(procedure_id, "procedure_id")
        user_id = ctx.user_id
        history = store.get_procedure_history(user_id, procedure_id, sub_user_id=sub_user_id)
        if not history:
            raise HTTPException(status_code=404, detail="procedure not found")
        evolution = store.get_procedure_evolution(user_id, procedure_id, sub_user_id=sub_user_id)
        return {"versions": history, "evolution_log": evolution}

    @app.get("/v1/procedures/{procedure_id}/evolution", tags=["Procedural Memory"])
    async def procedure_evolution(procedure_id: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Get the evolution log for a procedure — what changed and why."""
        _require_full_uuid(procedure_id, "procedure_id")
        if ctx.plan in ("free", "starter"):
            raise HTTPException(status_code=403, detail="Procedure evolution log is a Pro feature. Upgrade at mengram.io/dashboard")
        user_id = ctx.user_id
        evolution = store.get_procedure_evolution(user_id, procedure_id, sub_user_id=sub_user_id)
        return {"evolution": evolution}

    # ---- Unified Search (all 3 memory types) ----

    @app.post("/v1/search/all", tags=["Search"])
    async def search_all(req: SearchRequest, sub_user_id: str | None = Query(None), ctx: AuthContext = Depends(auth)):
        """Search across all memory types: semantic, episodic, and procedural.
        Returns categorized results from each memory system."""
        user_id = ctx.user_id
        use_quota(ctx, "search")  # atomic check+increment
        import hashlib as _hashlib

        sub_uid = _resolve_sub_user(req.user_id, sub_user_id)

        # Build metadata filters
        meta_filters = dict(req.filters) if req.filters else {}
        if req.agent_id:
            meta_filters["agent_id"] = req.agent_id
        if req.run_id:
            meta_filters["run_id"] = req.run_id
        if req.app_id:
            meta_filters["app_id"] = req.app_id

        # Validate optional threshold (additive — None = server defaults)
        if req.threshold is not None and not (0.0 <= req.threshold <= 1.0):
            raise HTTPException(status_code=400, detail="threshold must be between 0.0 and 1.0")

        # ---- Redis cache ----
        filter_str = json.dumps(meta_filters, sort_keys=True) if meta_filters else ""
        cache_input = f'{req.query}:{req.limit}:{req.graph_depth}:{req.threshold}:{filter_str}'
        cache_key = f"searchall:{user_id}:{sub_uid}:{_hashlib.md5(cache_input.encode('utf-8', errors='replace')).hexdigest()}"
        cached = store.cache.get(cache_key)
        if cached:
            sem = cached.get("semantic") or []
            top_score = float(sem[0]["score"]) if sem and "score" in sem[0] else 0.0
            store.log_usage(user_id, "search_all",
                            query_score=top_score,
                            query_language=_detect_query_language(req.query),
                            result_quality=_quality_label(top_score))
            return cached

        embedder = get_embedder()
        ep_limit = max(req.limit // 2, 3)
        proc_limit = max(req.limit // 2, 3)

        # Semantic (existing search)
        search_limit = max(req.limit * 2, 10)
        emb = None
        if embedder:
            try:
                emb = embedder.embed(req.query)
            except Exception as e:
                logger.error(f"Embedding failed in search_all: {e}")

        if emb is not None:
            sem_kwargs = dict(top_k=search_limit, query_text=req.query,
                              graph_depth=req.graph_depth, sub_user_id=sub_uid,
                              meta_filters=meta_filters)
            if req.threshold is not None:
                sem_kwargs["min_score"] = req.threshold
            semantic = store.search_vector_with_teams(user_id, emb, **sem_kwargs)
            # Fallback to looser threshold ONLY when client didn't pin one
            if not semantic and req.threshold is None:
                semantic = store.search_vector_with_teams(
                    user_id, emb, top_k=search_limit, min_score=0.2,
                    query_text=req.query, graph_depth=req.graph_depth, sub_user_id=sub_uid, meta_filters=meta_filters)
            # Episodic
            episodic = store.search_episodes_vector(
                user_id, emb, top_k=ep_limit, sub_user_id=sub_uid, query_text=req.query)
            # Procedural
            procedural = store.search_procedures_vector(
                user_id, emb, top_k=proc_limit, sub_user_id=sub_uid, query_text=req.query)
        else:
            semantic = store.search_text(user_id, req.query, top_k=search_limit, sub_user_id=sub_uid)
            episodic = store.search_episodes_text(
                user_id, req.query, top_k=ep_limit, sub_user_id=sub_uid)
            procedural = store.search_procedures_text(
                user_id, req.query, top_k=proc_limit, sub_user_id=sub_uid)

        # Split direct from graph-expanded, rerank only direct
        direct_sem = [r for r in semantic if not r.get("_graph")]
        graph_sem = [r for r in semantic if r.get("_graph")]
        if direct_sem and len(direct_sem) > 3:
            direct_sem = rerank_results(req.query, direct_sem, plan=ctx.plan)
        semantic = (direct_sem + graph_sem)[:req.limit]
        for r in semantic:
            r.pop("_graph", None)

        # Raw conversation chunk search (fallback for extraction misses)
        chunks = []
        try:
            if embedder and emb is not None:
                chunks = store.search_chunks_vector(
                    user_id, emb, query_text=req.query,
                    top_k=max(req.limit // 2, 5), sub_user_id=sub_uid)
        except Exception as e:
            logger.warning(f"Chunk search failed: {e}")

        # Unified ranking: normalize scores across types (different scales) and merge.
        # When `req.threshold` is set, filter items whose raw `score` is below it —
        # so threshold applies uniformly across all memory types, not just semantic.
        threshold_floor = req.threshold

        def _normalize_and_merge(sem, epi, proc, chk, limit):
            all_items = []
            for category, type_name in [(sem, "semantic"), (epi, "episodic"),
                                         (proc, "procedural"), (chk, "chunk")]:
                if not category:
                    continue
                max_s = max((r.get("score", 0) for r in category), default=0) or 1.0
                for r in category:
                    if threshold_floor is not None and r.get("score", 0) < threshold_floor:
                        continue
                    entry = dict(r)
                    entry["memory_type"] = type_name
                    entry["_norm"] = r.get("score", 0) / max_s
                    all_items.append(entry)
            all_items.sort(key=lambda r: r["_norm"], reverse=True)
            for r in all_items:
                r.pop("_norm", None)
            return all_items[:limit]

        # Compute top score across all categories so we can label the response
        # quality honestly. Without this field, callers (Vapi, MCP, dashboard)
        # can't tell a real match from arithmetic noise that slipped past the
        # filter — leading to the silent-bad-result churn pattern documented
        # in the search_vector floor fix.
        def _top_score(*cats):
            best = 0.0
            for cat in cats:
                if not cat:
                    continue
                v = cat[0].get("score") if isinstance(cat[0], dict) else 0
                try:
                    best = max(best, float(v or 0))
                except Exception:
                    pass
            return best

        overall_top = _top_score(semantic, episodic, procedural, chunks)
        if overall_top >= 0.3:
            result_quality = "strong"
        elif overall_top >= 0.15:
            result_quality = "weak"
        else:
            result_quality = "no_match"

        result = {
            "results": _normalize_and_merge(semantic, episodic, procedural, chunks, req.limit),
            "semantic": semantic,
            "episodic": episodic,
            "procedural": procedural,
            "chunks": chunks,
            "result_quality": result_quality,
            "top_score": round(overall_top, 4),
        }

        # Cache in Redis (TTL 30s)
        store.cache.set(cache_key, result, ttl=30)
        # Memory Health: log top semantic score + detected language
        top_score = float(semantic[0]["score"]) if semantic and "score" in semantic[0] else 0.0
        store.log_usage(user_id, "search_all",
                        query_score=top_score,
                        query_language=_detect_query_language(req.query),
                        result_quality=_quality_label(top_score))
        # increment already done in use_quota above
        if not any(result.get(k) for k in ("semantic", "episodic", "procedural", "chunks")):
            try:
                st = store.get_stats(user_id, sub_user_id=sub_uid)
                if (st.get("entities", 0) == 0 and st.get("facts", 0) == 0):
                    result["hint"] = (
                        'Your memory is empty — add something first, then search will find it. '
                        'Example: POST /v1/add_text with {"text": "I am a Python developer who uses PostgreSQL"} '
                        'then search for "what database do I use?"'
                    )
                else:
                    result["hint"] = (
                        f"No results matched your query. Try broader terms or different phrasing. "
                        f"Your memory has {st.get('entities', 0)} entities and {st.get('facts', 0)} facts."
                    )
            except Exception:
                result["hint"] = "No memories found. Add your first memory with POST /v1/add — then search will return results."
        return result

    # ============================================
    # Vapi Voice Integration — webhook adapters
    # ============================================
    # Vapi calls these endpoints when its assistants invoke our tools or
    # post call lifecycle events. We translate Vapi's webhook format
    # (toolCallList + call.customer.number) to our existing search/add
    # paths, keyed per caller via sub_user_id="voice:<E.164>".
    #
    # This is the integration that backs mengram.io/integrations/vapi.
    # No new storage, no new pipeline — only adapters over existing
    # extraction + retrieval.

    class _VapiCustomer(BaseModel):
        number: str | None = None

    class _VapiCall(BaseModel):
        customer: _VapiCustomer | None = None
        id: str | None = None
        type: str | None = None  # inboundPhoneCall, outboundPhoneCall, webCall

    class _VapiWebhookMessage(BaseModel):
        # Vapi posts MANY event types to the same server URL:
        # tool-calls, end-of-call-report, transcript, status-update,
        # conversation-update, etc. We dispatch by `type` in each endpoint.
        type: str | None = None
        # Tool call payloads: Vapi sends BOTH (per docs) — `toolCalls` follows
        # OpenAI spec (nested `function.{name, arguments}`), `toolCallList` is
        # the flattened convenience form (`name`, `arguments` at top level).
        # Accept dicts and dispatch via _extract_vapi_tool_call below.
        toolCalls: list[dict] | None = None
        toolCallList: list[dict] | None = None
        call: _VapiCall | None = None
        # Transcript paths: streaming `transcript` events carry partial text
        # at `message.transcript`. `end-of-call-report` carries the final
        # transcript at `message.transcript` AND `message.artifact.transcript`.
        transcript: str | None = None
        artifact: dict | None = None
        transcriptType: str | None = None  # "partial" | "final" on transcript events

    class VapiWebhookRequest(BaseModel):
        message: _VapiWebhookMessage

    def _extract_vapi_tool_call(msg: "_VapiWebhookMessage"):
        """Pull (tool_call_id, function_name, arguments_dict) from either Vapi
        tool-call shape. Returns None if no tool call present.

        Vapi tool-calls events include BOTH `toolCalls` (OpenAI-spec nested)
        and `toolCallList` (flattened) per
        https://github.com/VapiAI/docs/blob/main/fern/tools/custom-tools.mdx.
        We prefer `toolCalls` because it's canonical, but fall back to the
        flattened form so payloads with only one shape still work.
        """
        tc_list = msg.toolCalls or msg.toolCallList or []
        if not tc_list or not isinstance(tc_list[0], dict):
            return None
        tc = tc_list[0]
        tc_id = tc.get("id", "") or ""
        fn = tc.get("function")
        if isinstance(fn, dict):
            name = fn.get("name", "") or ""
            args = fn.get("arguments", {})
        else:
            name = tc.get("name", "") or ""
            args = tc.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
        return tc_id, name, args

    def _voice_sub_user(phone: str) -> str:
        """Per-caller sub_user_id. Normalizes to digits + leading + only,
        so '+1 (415) 555-1234' and '14155551234' map to the same memory."""
        if not phone:
            return "voice:unknown"
        normalized = "".join(c for c in phone.strip() if c.isdigit() or c == "+")
        return f"voice:{normalized}" if normalized else "voice:unknown"

    @app.post("/v1/voice/vapi/recall", tags=["Voice"])
    async def vapi_recall(req: VapiWebhookRequest, ctx: AuthContext = Depends(auth)):
        """Vapi webhook: returns concise caller context for the AI agent.

        Dispatches by message.type. Only handles `tool-calls` events; other
        event types Vapi posts to the same server URL (status-update,
        conversation-update, transcript, etc.) get a benign 200 response so
        Vapi keeps the assistant alive rather than dropping context.

        Vapi tool result MUST be a single string per
        https://docs.vapi.ai/tools/custom-tools-troubleshooting — any
        non-string result is silently dropped by Vapi.
        """
        msg = req.message
        # Reject only events explicitly NOT for this endpoint; treat
        # missing/unknown type as `tool-calls` so curl tests still work.
        if msg.type and msg.type != "tool-calls":
            return {"status": "ignored", "reason": f"event {msg.type} not handled by recall endpoint"}

        tool_call = _extract_vapi_tool_call(msg)
        if not tool_call:
            # No tool call payload — return 200 not 4xx, so Vapi doesn't
            # mark the assistant as broken.
            return {"status": "ignored", "reason": "no tool call present"}
        tc_id, fn_name, args = tool_call

        phone = (args.get("phone") or "").strip()
        if not phone and msg.call and msg.call.customer:
            phone = (msg.call.customer.number or "").strip()

        # Web calls (browser SDK) have no customer.number — fall back to
        # call.id so each web session at least gets its own scope rather
        # than sharing one global "voice:unknown" bucket.
        if not phone and msg.call and msg.call.id:
            return {"results": [{"toolCallId": tc_id,
                                  "result": "Web caller — no phone number yet, no prior context."}]}
        if not phone:
            return {"results": [{"toolCallId": tc_id,
                                  "result": "Unknown caller — no phone number available."}]}

        sub_uid = _voice_sub_user(phone)
        use_quota(ctx, "search")

        # For a known caller (phone-keyed), we don't want semantic relevance —
        # we want EVERYTHING we know about them. Generic vector queries like
        # "important facts about caller" match poorly against real fact
        # embeddings ("prefers morning slots"). Fetch entities + facts directly
        # by sub_user_id, then optionally augment with recent episodes for
        # narrative context.
        try:
            entities = store.get_all_entities_full(ctx.user_id, sub_user_id=sub_uid) or []
        except Exception as e:
            logger.error(f"vapi_recall fetch failed for phone={phone}: {e}")
            return {"results": [{"toolCallId": tc_id,
                                  "result": "Memory lookup failed — proceed without context."}]}

        # Sort person entities first so the caller's own facts surface ahead
        # of related entities. Among persons, sort by fact count descending —
        # the caller almost always accumulates more facts about themselves
        # than they accumulate about people they mention (daughter, doctor,
        # etc.), so most-facts-wins is a reliable caller-vs-mentioned-person
        # heuristic without needing explicit caller tagging.
        def _is_person(e):
            return (e.get("type") or "").lower() == "person"
        entities_sorted = sorted(
            entities,
            key=lambda e: (
                0 if _is_person(e) else 1,
                -len(e.get("facts") or []),
            )
        )

        # Build a compact context string the assistant can verbalize.
        # Skip the reserved _reflections entity, cap facts per entity.
        fact_lines = []
        person_name = None
        for e in entities_sorted:
            name = e.get("entity") or ""
            if name == "_reflections" or not name:
                continue
            entity_facts = e.get("facts") or []
            if not entity_facts:
                continue
            if _is_person(e) and person_name is None:
                person_name = name
            # Up to 5 facts for the caller (person), 2 for related entities.
            cap = 5 if _is_person(e) else 2
            for f in entity_facts[:cap]:
                content = f if isinstance(f, str) else (f.get("content", "") if isinstance(f, dict) else str(f))
                content = content.strip()
                if not content:
                    continue
                fact_lines.append(f"{name}: {content}")

        if not fact_lines:
            summary = f"New caller — no prior context for {phone}."
        else:
            header = f"Known about caller ({person_name or phone}):"
            summary = header + " " + " | ".join(fact_lines)
            # 900-char cap — Vapi tool result needs to fit comfortably in
            # the assistant's prompt without bloating tokens. 900 chars ≈
            # ~225 tokens, leaves room for system prompt + other context.
            if len(summary) > 900:
                summary = summary[:897] + "..."

        try:
            store.log_usage(ctx.user_id, "voice_recall", query_score=1.0)
        except Exception:
            pass

        return {
            "results": [{
                "toolCallId": tc_id,
                "result": summary,
            }]
        }

    @app.post("/v1/voice/vapi/save", tags=["Voice"])
    async def vapi_save(req: VapiWebhookRequest, ctx: AuthContext = Depends(auth)):
        """Vapi end-of-call webhook: extract memories from the transcript.

        Only processes `end-of-call-report` events. Vapi streams MANY events
        to the same server URL — status-update, conversation-update, partial
        transcript chunks (`message.type == "transcript"`, `transcriptType:
        partial`), speech-update, hang, etc. Without a type guard, the save
        endpoint would re-run extraction on every partial transcript chunk
        (dozens of times per call), burning quota and creating duplicate
        memory entries. The guard makes all other events benign no-ops.

        For end-of-call-report the final transcript lives at
        `message.transcript` AND `message.artifact.transcript` per
        https://github.com/VapiAI/docs/blob/main/fern/server-url/events.mdx.
        We read whichever is present.
        """
        msg = req.message

        # Type guard: only end-of-call-report (or unspecified, for curl
        # tests) triggers extraction. Everything else gets ignored with a
        # 200 so Vapi doesn't mark the assistant as broken.
        if msg.type and msg.type != "end-of-call-report":
            return {"status": "ignored", "reason": f"event {msg.type} not handled by save endpoint"}

        # Transcript: prefer top-level, fall back to artifact.transcript.
        transcript = (msg.transcript or "").strip()
        if not transcript and isinstance(msg.artifact, dict):
            transcript = (msg.artifact.get("transcript") or "").strip()

        phone = ""
        if msg.call and msg.call.customer:
            phone = (msg.call.customer.number or "").strip()

        if not phone or not transcript:
            return {"status": "ignored", "reason": "missing phone or transcript"}

        sub_uid = _voice_sub_user(phone)
        use_quota(ctx, "add")

        job_id = store.create_job(ctx.user_id, "add")
        normalized_phone = "".join(c for c in phone if c.isdigit() or c == "+")
        metadata = {
            "source": "voice_call",
            "agent_id": "vapi",
            "phone": normalized_phone,
            "call_id": req.message.call.id if req.message.call else None,
        }

        import threading

        def process_in_background():
            _run_extraction_pipeline(
                user_id=ctx.user_id,
                sub_uid=sub_uid,
                conversation=[{"role": "user", "content": _sanitize_text(transcript)}],
                metadata=metadata,
                expiration_date=None,
                job_id=job_id,
                plan=ctx.plan,
                prompt_version="v1",
            )

        threading.Thread(target=process_in_background, daemon=True).start()

        from starlette.responses import JSONResponse
        return JSONResponse(status_code=202, content={
            "status": "accepted",
            "job_id": job_id,
            "sub_user_id": sub_uid,
        })

    @app.api_route("/integrations/vapi", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def integrations_vapi():
        """Vapi integration landing page."""
        page_path = Path(__file__).parent / "integrations-vapi.html"
        if not page_path.exists():
            raise HTTPException(404, "page not found")
        html = page_path.read_text(encoding="utf-8")
        html = html.replace("{{VERSION}}", __version__).replace("{{BASE_URL}}", BASE_URL)
        return html

    # ============================================
    # Smart Memory Triggers (v2.6)
    # ============================================

    @app.get("/v1/triggers", tags=["Smart Triggers"])
    async def get_own_triggers(include_fired: bool = False,
                               limit: int = 50, sub_user_id: str = Query("default"),
                               ctx: AuthContext = Depends(auth)):
        """Get smart triggers for the authenticated user."""
        if ctx.plan in ("free", "starter"):
            raise HTTPException(status_code=403, detail="Smart Triggers is a Pro feature. Upgrade at mengram.io/dashboard")
        user_id = ctx.user_id
        triggers = store.get_triggers(user_id, include_fired=include_fired, limit=limit, sub_user_id=sub_user_id)
        for t in triggers:
            for key in ("fire_at", "fired_at", "created_at"):
                if t.get(key) and hasattr(t[key], "isoformat"):
                    t[key] = t[key].isoformat()
        return {"triggers": triggers, "count": len(triggers)}

    @app.get("/v1/triggers/{target_user_id}", tags=["Smart Triggers"])
    async def get_triggers(target_user_id: str, include_fired: bool = False,
                           limit: int = 50, sub_user_id: str = Query("default"),
                           ctx: AuthContext = Depends(auth)):
        """Get smart triggers for a specific user (must be your own user_id or a sub_user_id)."""
        if ctx.plan in ("free", "starter"):
            raise HTTPException(status_code=403, detail="Smart Triggers is a Pro feature. Upgrade at mengram.io/dashboard")
        user_id = ctx.user_id
        # Authorization: only allow accessing own triggers
        if target_user_id != user_id:
            raise HTTPException(status_code=403, detail="Cannot access other users' triggers")
        triggers = store.get_triggers(user_id, include_fired=include_fired, limit=limit, sub_user_id=sub_user_id)
        for t in triggers:
            for key in ("fire_at", "fired_at", "created_at"):
                if t.get(key) and hasattr(t[key], "isoformat"):
                    t[key] = t[key].isoformat()
        return {"triggers": triggers, "count": len(triggers)}

    @app.post("/v1/triggers/process", tags=["Smart Triggers"])
    async def process_triggers(ctx: AuthContext = Depends(auth)):
        """Process pending triggers for the authenticated user only."""
        if ctx.plan in ("free", "starter"):
            raise HTTPException(status_code=403, detail="Smart Triggers is a Pro feature. Upgrade at mengram.io/dashboard")
        user_id = ctx.user_id
        result = store.process_user_triggers(user_id)
        return result

    @app.delete("/v1/triggers/{trigger_id}", tags=["Smart Triggers"])
    async def dismiss_trigger(trigger_id: int, ctx: AuthContext = Depends(auth)):
        """Dismiss (mark as fired) a specific trigger without sending webhook."""
        user_id = ctx.user_id
        store.ensure_triggers_table()
        with store._cursor() as cur:
            cur.execute("""
                UPDATE memory_triggers SET fired = TRUE, fired_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id
            """, (trigger_id, user_id))
            row = cur.fetchone()
        if row:
            return {"status": "dismissed", "id": trigger_id}
        raise HTTPException(status_code=404, detail="Trigger not found")

    @app.post("/v1/triggers/detect/{target_user_id}", tags=["Smart Triggers"])
    async def detect_triggers_debug(target_user_id: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Manually run trigger detection for the authenticated user. Returns detailed results."""
        user_id = ctx.user_id
        # Authorization: only allow detecting own triggers
        if target_user_id != user_id:
            raise HTTPException(status_code=403, detail="Cannot detect triggers for other users")
        results = {"reminders": 0, "contradictions": 0, "patterns": 0, "errors": []}
        try:
            results["reminders"] = store.detect_reminder_triggers(user_id, sub_user_id=sub_user_id)
        except Exception as e:
            results["errors"].append(f"reminders: {e}")
        try:
            results["patterns"] = store.detect_pattern_triggers(user_id, sub_user_id=sub_user_id)
        except Exception as e:
            results["errors"].append(f"patterns: {e}")
        triggers = store.get_triggers(user_id, sub_user_id=sub_user_id)
        results["total_pending"] = len(triggers)
        results["triggers"] = triggers
        # Serialize datetimes
        for t in results["triggers"]:
            for key in ("fire_at", "fired_at", "created_at"):
                if t.get(key) and hasattr(t[key], "isoformat"):
                    t[key] = t[key].isoformat()
        return results

    # ---- Background cron jobs (with PG advisory lock to run on one worker only) ----
    # MENGRAM_ROLE: "api" = HTTP only (no cron), "cron" = cron only, "all" = both (default).
    # Default "all" preserves existing behavior so this change is safe to deploy without
    # any env var set. Set MENGRAM_ROLE=api on the web service + run a separate service
    # with MENGRAM_ROLE=cron to split cron into a dedicated Railway service.
    _MENGRAM_ROLE = os.environ.get("MENGRAM_ROLE", "all").lower()
    _CRON_ENABLED = _MENGRAM_ROLE in ("all", "cron")
    if not _CRON_ENABLED:
        logger.info(f"⏭️  Cron jobs disabled on this instance (MENGRAM_ROLE={_MENGRAM_ROLE})")
    import threading, time as _time

    def _try_advisory_lock(lock_id: int):
        """Try to acquire a PG session-level advisory lock (non-blocking).
        Returns the dedicated connection holding the lock, or None on failure.
        Caller must keep the connection alive — lock releases when it closes."""
        try:
            import psycopg2 as _pg2
            conn = _pg2.connect(store.database_url)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
                if cur.fetchone()[0]:
                    return conn
            conn.close()
            return None
        except Exception:
            return None

    def _lock_still_held(conn) -> bool:
        """Whether the lock connection is still usable. The Supabase pooler
        drops idle connections, which silently releases the advisory lock — a
        loop that never rechecks keeps running while another instance is free
        to grab the same lock and double-fire."""
        if conn is None or conn.closed:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            return False

    # Lock IDs (arbitrary unique ints)
    _LOCK_TRIGGER_CRON = 900001
    _LOCK_DRIP_CRON = 900002
    _LOCK_HEALTH_CRON = 900003

    def _trigger_cron_loop():
        """Background thread that processes triggers every 5 minutes."""
        _time.sleep(30)  # Initial delay to let server start
        # The lock is retried every tick rather than once at startup: on a
        # rolling deploy the incoming instance loses the race to the outgoing
        # one, and giving up left this cron dead until the next restart.
        _lock_conn = None
        while True:
            if not _lock_still_held(_lock_conn):
                _lock_conn = _try_advisory_lock(_LOCK_TRIGGER_CRON)
                if not _lock_conn:
                    _time.sleep(300)
                    continue
                logger.info("🧠 Smart trigger cron started (every 5 min)")
            try:
                result = store.process_all_triggers()
                if result["fired"] > 0:
                    logger.info(f"🧠 Trigger cron: fired {result['fired']} triggers")
            except Exception as e:
                logger.error(f"⚠️ Trigger cron error: {e}")
            _time.sleep(300)  # Every 5 minutes

    if _CRON_ENABLED:
        _cron_thread = threading.Thread(target=_trigger_cron_loop, daemon=True)
        _cron_thread.start()

    # ---- Background drip email cron ----

    def _drip_email_cron_loop():
        """Background thread that sends onboarding drip emails every 30 minutes."""
        _time.sleep(60)  # Initial delay
        _lock_conn = None  # reacquired every tick — see _trigger_cron_loop
        while True:
            if not _lock_still_held(_lock_conn):
                _lock_conn = _try_advisory_lock(_LOCK_DRIP_CRON)
                if not _lock_conn:
                    _time.sleep(300)
                    continue
                logger.info("📧 Onboarding drip email cron started (every 30 min)")
            try:
                import secrets as _secrets

                # Completed signups with no API activity
                for user in store.get_inactive_completed_signups(24, "completed_24h"):
                    if store.try_record_drip(user["email"], "completed_24h", user["id"]):
                        _send_drip_email(user["email"], "completed_24h")
                        _time.sleep(0.5)  # Resend rate limit: 5 req/s

                for user in store.get_inactive_completed_signups(72, "completed_72h"):
                    if store.try_record_drip(user["email"], "completed_72h", user["id"]):
                        _send_drip_email(user["email"], "completed_72h")
                        _time.sleep(0.5)

                for user in store.get_inactive_completed_signups(168, "completed_7d"):
                    if store.try_record_drip(user["email"], "completed_7d", user["id"]):
                        _send_drip_email(user["email"], "completed_7d")
                        _time.sleep(0.5)

                # Incomplete signups (verification pending)
                for row in store.get_incomplete_signups_for_drip(1, "incomplete_1h"):
                    if store.try_record_drip(row["email"], "incomplete_1h"):
                        code = f"{_secrets.randbelow(900000) + 100000}"
                        store.save_email_code(row["email"], code)
                        _send_drip_email(row["email"], "incomplete_1h", code=code)
                        _time.sleep(0.5)

                for row in store.get_incomplete_signups_for_drip(24, "incomplete_24h"):
                    if store.try_record_drip(row["email"], "incomplete_24h"):
                        code = f"{_secrets.randbelow(900000) + 100000}"
                        store.save_email_code(row["email"], code)
                        _send_drip_email(row["email"], "incomplete_24h", code=code)
                        _time.sleep(0.5)

                # Engagement drips: users who did one action but not the other
                for user in store.get_users_added_no_search():
                    if store.try_record_drip(user["email"], "added_no_search", user["id"]):
                        _send_drip_email(user["email"], "added_no_search")
                        _time.sleep(0.5)

                for user in store.get_users_searched_no_add():
                    if store.try_record_drip(user["email"], "searched_no_add", user["id"]):
                        _send_drip_email(user["email"], "searched_no_add")
                        _time.sleep(0.5)

                # Churned active users (were active, stopped for 7/14/30 days)
                for user in store.get_churned_active_users():
                    if store.try_record_drip(user["email"], "churned_7d", user["id"]):
                        _send_drip_email(user["email"], "churned_7d")
                        _time.sleep(0.5)

                for user in store.get_churned_active_users(inactive_hours=336, drip_type="churned_14d"):
                    if store.try_record_drip(user["email"], "churned_14d", user["id"]):
                        _send_drip_email(user["email"], "churned_14d")
                        _time.sleep(0.5)

                for user in store.get_churned_active_users(inactive_hours=720, drip_type="churned_30d"):
                    if store.try_record_drip(user["email"], "churned_30d", user["id"]):
                        _send_drip_email(user["email"], "churned_30d")
                        _time.sleep(0.5)

                # Abandoned Paddle checkouts (started upgrade but never paid)
                for row in store.get_abandoned_checkouts(hours=1, drip_type="checkout_abandoned_1h"):
                    if store.try_record_drip(row["email"], "checkout_abandoned_1h", row["user_id"]):
                        _send_drip_email(row["email"], "checkout_abandoned_1h", user_id=row["user_id"], plan=row["plan"])
                        _time.sleep(0.5)

                for row in store.get_abandoned_checkouts(hours=24, drip_type="checkout_abandoned_24h"):
                    if store.try_record_drip(row["email"], "checkout_abandoned_24h", row["user_id"]):
                        _send_drip_email(row["email"], "checkout_abandoned_24h", user_id=row["user_id"], plan=row["plan"])
                        _time.sleep(0.5)

                # Day 4 — weekly Memory Health digest for degraded/critical users.
                # Fires Mondays 09:00–10:00 UTC only; deduped per ISO week via drip_type suffix.
                _now_utc = datetime.datetime.now(datetime.timezone.utc)
                if _now_utc.weekday() == 0 and 9 <= _now_utc.hour < 10:
                    _iso = _now_utc.strftime("%G-W%V")  # e.g. "2026-W19"
                    _digest_type = f"health_digest_{_iso}"
                    for row in store.get_users_for_health_digest():
                        if store.try_record_drip(row["email"], _digest_type, row["user_id"]):
                            _send_drip_email(
                                row["email"],
                                "health_digest_degraded",
                                code=row["summary"],
                                user_id=row["user_id"],
                                plan=row["recommendations"],
                            )
                            _time.sleep(0.5)

                # Weekly Insights digest — pairs with the Dream Cycle reflection cron.
                # OFF BY DEFAULT — owner felt the test send looked spammy. Code and
                # helper remain so we can re-enable once the email is reshaped
                # (e.g. opt-in only, lower frequency, or surfaced in-app instead).
                # Flip INSIGHTS_DIGEST_ENABLED=true to fire on Monday 09:00–10:00 UTC.
                if (os.environ.get("INSIGHTS_DIGEST_ENABLED", "false").lower() == "true"
                        and _now_utc.weekday() == 0 and 9 <= _now_utc.hour < 10):
                    import json as _json_ins
                    _iso_ins = _now_utc.strftime("%G-W%V")
                    _insights_drip = f"insights_digest_{_iso_ins}"
                    for row in store.get_users_for_insights_digest():
                        if store.try_record_drip(row["email"], _insights_drip, row["user_id"]):
                            _send_drip_email(
                                row["email"],
                                "insights_digest",
                                code=str(row["new_insights"]),
                                user_id=row["user_id"],
                                plan=_json_ins.dumps(row["samples"], default=str),
                            )
                            _time.sleep(0.5)

                # Weekly founder ops report — silence alarms. Accounts whose
                # silence IS the signal: keys that never made a call (broken on
                # install) and previously-active users gone quiet. Sent to the
                # founder only, never to users. Mondays, deduped per ISO week.
                if _now_utc.weekday() == 0 and 9 <= _now_utc.hour < 10:
                    _iso_ops = _now_utc.strftime("%G-W%V")
                    _ops_email = "the.baizhanov@gmail.com"
                    if store.try_record_drip(_ops_email, f"founder_silence_{_iso_ops}"):
                        try:
                            _rep = store.get_silence_report()
                            _broken = _rep["broken_on_install"]
                            _quiet = _rep["gone_quiet"]
                            _half = _rep.get("half_wired", [])
                            if (_broken or _quiet or _half) and os.environ.get("RESEND_API_KEY"):
                                _lines = [f"Silence report {_iso_ops}", ""]
                                _lines.append(f"Broken on install — signed up 48h+ ago, zero API calls ({len(_broken)}):")
                                _lines += [f"  - {r['email']} (signed up {r['signed_up']})" for r in _broken] or ["  (none)"]
                                _lines.append("")
                                _lines.append(f"Gone quiet — 20+ calls before, silent 14+ days ({len(_quiet)}):")
                                _lines += [f"  - {r['email']} (last active {r['last_active']}, {r['total_calls']} calls)" for r in _quiet] or ["  (none)"]
                                _lines.append("")
                                _lines.append(f"Half-wired — recall integrated, capture never ({len(_half)}; searching an empty vault):")
                                _lines += [f"  - {r['email']} ({r['searches']} searches / 14d, zero entities)" for r in _half] or ["  (none)"]
                                import resend as _resend_ops
                                _resend_ops.api_key = os.environ["RESEND_API_KEY"]
                                _resend_ops.Emails.send({
                                    "from": os.environ.get("EMAIL_FROM", "Mengram <noreply@mengram.io>"),
                                    "to": [_ops_email],
                                    "subject": f"[mengram ops] Silence report {_iso_ops}: "
                                               f"{len(_broken)} broken installs, {len(_quiet)} gone quiet",
                                    "text": "\n".join(_lines),
                                })
                                logger.info(f"📭 Founder silence report sent: {len(_broken)} broken, {len(_quiet)} quiet")
                        except Exception as _ops_e:
                            logger.error(f"⚠️ Founder silence report error: {_ops_e}")

            except Exception as e:
                logger.error(f"⚠️ Drip email cron error: {e}")
            _time.sleep(1800)  # Every 30 minutes

    if _CRON_ENABLED:
        _drip_thread = threading.Thread(target=_drip_email_cron_loop, daemon=True)
        _drip_thread.start()

    # ---- Memory Health Aggregation Cron (Day 2 of Memory Health Monitor) ----
    def _memory_health_cron_loop():
        """Aggregates per-user retrieval health every 6 hours.

        For each user with search activity in the last 24h, computes:
          - mean / median / std-dev of top-result query_score
          - language breakdown (% of searches per language)
          - low-quality session count (score < 0.4)
          - overall_status: healthy (mean >= 0.6) / degraded (>= 0.4) / critical (< 0.4)
          - recommendations: actionable steps when degraded/critical

        Writes/upserts to `memory_health` table. Downstream:
          Day 3 dashboard widget reads this table.
          Day 4 weekly digest emails read this table.
          Day 5 /v1/health/retrieval endpoint reads this table.
        """
        import traceback
        logger.info("🩺 Memory Health cron: thread alive, sleeping 120s before first run")
        _time.sleep(120)  # Initial delay so this doesn't pile on with drip cron
        _lock_conn = None  # reacquired every tick — see _trigger_cron_loop
        while True:
            if not _lock_still_held(_lock_conn):
                _lock_conn = _try_advisory_lock(_LOCK_HEALTH_CRON)
                if not _lock_conn:
                    _time.sleep(300)
                    continue
                logger.info("🩺 Memory Health aggregation cron started (every 6h, 5min retry on error)")
            iteration_failed = False
            try:
                result = store.aggregate_memory_health(window_hours=24)
                logger.info(
                    f"🩺 Memory Health: tick OK — users_updated={result.get('users_updated', 0)} "
                    f"healthy={result.get('healthy', 0)} degraded={result.get('degraded', 0)} "
                    f"critical={result.get('critical', 0)}"
                )
            except Exception as e:
                iteration_failed = True
                logger.error(
                    f"⚠️ Memory Health cron error: {type(e).__name__}: {e}\n"
                    f"{traceback.format_exc()}"
                )
            # Retry quickly on failure so we don't lose 6h every time something breaks.
            _time.sleep(300 if iteration_failed else 21600)

    if _CRON_ENABLED:
        _health_thread = threading.Thread(target=_memory_health_cron_loop, daemon=True)
        _health_thread.start()

    # ---- Background reflection cron (Dream Cycle equivalent) ----

    _LOCK_REFLECTION_CRON = 900004
    _REFLECTION_ENABLED = os.environ.get("REFLECTION_CRON_ENABLED", "true").lower() != "false"
    _REFLECTION_BATCH_SIZE = int(os.environ.get("REFLECTION_BATCH_SIZE", "50"))

    def _reflection_cron_loop():
        """Daily sweep that refreshes the reflection (insight) layer for active
        users whose facts have evolved since last reflection.

        Reflection is already auto-triggered on /v1/add, but that only fires
        while the user is actively writing. This cron catches everyone else —
        users who accumulated facts via earlier sessions and stopped adding,
        whose entity/cross/temporal summaries silently go stale.

        Selection logic lives in store.get_users_due_for_reflection — it mirrors
        should_reflect's triggers exactly, plus an "active in last 14 days"
        filter so dormant accounts don't burn LLM calls. Per-user generation
        runs through the existing generate_reflections pipeline, so prompt and
        storage stay consistent with the in-band /v1/add path.
        """
        import traceback
        logger.info("🌙 Reflection cron: thread alive, sleeping 180s before first run")
        _time.sleep(180)
        _lock_conn = None  # reacquired every tick — see _trigger_cron_loop
        while True:
            if not _lock_still_held(_lock_conn):
                _lock_conn = _try_advisory_lock(_LOCK_REFLECTION_CRON)
                if not _lock_conn:
                    _time.sleep(300)
                    continue
                logger.info(
                    f"🌙 Reflection cron started (every 24h, batch up to {_REFLECTION_BATCH_SIZE} users)"
                )
            iteration_failed = False
            try:
                users_due = store.get_users_due_for_reflection(
                    max_users=_REFLECTION_BATCH_SIZE
                )
                logger.info(f"🌙 Reflection: {len(users_due)} users due for refresh")

                # get_llm() returns the extractor; the real LLM client (with
                # .complete()) lives on extractor.llm — same pattern used by
                # the auto-reflection path in /v1/add (api.py:6532, 7489).
                extractor = get_llm()
                llm_client = extractor.llm
                reflected = 0
                quota_skipped = 0
                error_skipped = 0

                for u in users_due:
                    uid = u["user_id"]
                    sub_uid = u.get("sub_user_id") or "default"
                    try:
                        sub = store.get_subscription(uid) or {}
                        plan = sub.get("plan", "free")
                        max_reflects = PLAN_QUOTAS.get(plan, PLAN_QUOTAS["free"]).get("reflects", 0)
                        if max_reflects == 0:
                            quota_skipped += 1
                            continue
                        try:
                            store.check_and_increment(uid, "reflect", max_reflects)
                        except ValueError:
                            quota_skipped += 1
                            continue

                        result = store.generate_reflections(
                            user_id=uid,
                            llm_client=llm_client,
                            sub_user_id=sub_uid,
                        )
                        reflected += 1
                        logger.info(
                            f"🌙 Reflected user={uid[:8]} sub={sub_uid} "
                            f"new_facts={u['new_facts']} → "
                            f"entity={len(result.get('entity_reflections', []))} "
                            f"cross={len(result.get('cross_entity', []))} "
                            f"temporal={len(result.get('temporal', []))}"
                        )
                    except Exception as e:
                        error_skipped += 1
                        logger.warning(
                            f"🌙 Reflection failed for user={uid[:8]}: {type(e).__name__}: {e}"
                        )

                logger.info(
                    f"🌙 Reflection tick OK — reflected={reflected} "
                    f"quota_skipped={quota_skipped} error_skipped={error_skipped}"
                )
            except Exception as e:
                iteration_failed = True
                logger.error(
                    f"⚠️ Reflection cron error: {type(e).__name__}: {e}\n"
                    f"{traceback.format_exc()}"
                )
            # Retry quickly on failure, otherwise daily.
            _time.sleep(900 if iteration_failed else 86400)

    if _CRON_ENABLED and _REFLECTION_ENABLED:
        _reflection_thread = threading.Thread(target=_reflection_cron_loop, daemon=True)
        _reflection_thread.start()
    elif _CRON_ENABLED and not _REFLECTION_ENABLED:
        logger.info("🌙 Reflection cron disabled via REFLECTION_CRON_ENABLED=false")

    # ---- Billing & Subscription: cloud/billing.py ----
    app.include_router(billing_router(store, auth))

    @app.get("/v1/health/retrieval", tags=["System"])
    async def get_retrieval_health(ctx: AuthContext = Depends(auth)):
        """Memory Health Monitor — per-user retrieval quality snapshot.

        Aggregated every 6h from the trailing 24h window of scored searches.
        Helps detect silent quality drops (every search returns 200, but
        relevance is degrading). See blog post on Memory Health Monitor
        for the rationale.

        Returns 404 if user has fewer than 5 scored searches in the window
        (insufficient signal — no snapshot computed yet).
        """
        snap = store.get_memory_health(ctx.user_id)
        if not snap:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "no_health_snapshot",
                    "message": "Need at least 5 scored searches in the last 24h before a health snapshot can be computed. Run some searches and check back in 6 hours.",
                },
            )
        return snap

    # ---- MCP over HTTP (SSE transport for Smithery / remote MCP clients) ----

    # ---- MCP Discovery Manifest (well-known, for auto-discovery by agents/crawlers) ----
    #
    # Forward-compatible connection manifest describing this server's MCP endpoints,
    # transports, and authentication. Complements the Smithery server-card.json
    # (which is a tools catalog). When the Anthropic MCP discovery spec lands,
    # we update fields here — path stays stable.
    #
    # Agents/browsers/marketplaces can fetch this to auto-discover how to connect.

    @app.get("/.well-known/mcp")
    async def mcp_discovery_manifest():
        return {
            "name": "mengram",
            "title": "Mengram — AI Memory Layer",
            "version": __version__,
            "description": (
                "Persistent memory layer for AI agents. Three memory types "
                "(semantic facts, episodic events, procedural workflows), "
                "knowledge graph, cognitive profile, smart triggers. "
                "Works with Claude Desktop, Cursor, Windsurf, and any MCP client."
            ),
            "homepage": "https://mengram.io",
            "documentation": "https://mengram.io/docs/mcp-server",
            "icon": "https://mengram.io/static/icon-512.png",
            "transports": [
                {
                    "type": "streamable-http",
                    "url": "https://mengram.io/mcp",
                },
                {
                    "type": "sse",
                    "url": "https://mengram.io/mcp/sse",
                    "messages_url": "https://mengram.io/mcp/messages/",
                },
            ],
            "authentication": {
                "required": True,
                "schemes": ["bearer"],
                "header": "Authorization",
                "signup_url": "https://mengram.io/#signup",
            },
            "capabilities": {
                "tools": True,
                "resources": True,
                "prompts": False,
            },
            "tools_card_url": "https://mengram.io/.well-known/mcp/server-card.json",
            "contact": {
                "support_email": "support@mengram.io",
                "issues": "https://github.com/alibaizhanov/mengram/issues",
            },
        }

    # ---- MCP Server Card (for Smithery discovery) ----

    @app.get("/.well-known/mcp/server-card.json")
    async def mcp_server_card():
        return {
            "serverInfo": {
                "name": "mengram",
                "title": "Mengram — AI Memory Layer",
                "version": __version__,
                "description": "Give AI agents memory that actually learns. 3 memory types: semantic (facts & preferences), episodic (events & decisions), and procedural (workflows that evolve from failures). Cognitive Profile, Smart Triggers, Memory Agents, Knowledge Graph. Cloud API.",
                "homepage": "https://mengram.io",
                "icon": "https://mengram.io/static/icon-512.png",
            },
            "authentication": {"required": True, "schemes": ["bearer"]},
            "tools": [
                {"name": "remember", "description": "Save knowledge from conversation to cloud memory. Auto-extracts facts, events, and workflows.",
                 "annotations": {"title": "Remember Conversation", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"conversation": {"type": "array", "description": "List of messages with role and content", "items": {"type": "object", "properties": {"role": {"type": "string", "description": "Message role: user or assistant"}, "content": {"type": "string", "description": "Message text content"}}, "required": ["role", "content"]}}}, "required": ["conversation"]}},
                {"name": "remember_text", "description": "Remember knowledge from plain text. Extracts entities, facts, and relations.",
                 "annotations": {"title": "Remember Text", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "Plain text to extract knowledge from"}}, "required": ["text"]}},
                {"name": "recall", "description": "Semantic search through cloud memory. Use specific keywords like names, projects, technologies.",
                 "annotations": {"title": "Recall Memory", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query — use specific names, projects, or topics"}}, "required": ["query"]}},
                {"name": "search", "description": "Structured semantic search — returns JSON results with similarity scores, facts, and knowledge.",
                 "annotations": {"title": "Search Memory", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query text"}, "top_k": {"type": "integer", "default": 5, "description": "Maximum number of results to return"}}, "required": ["query"]}},
                {"name": "search_all", "description": "Unified search across all 3 memory types — semantic, episodic, and procedural. Best for broad queries.",
                 "annotations": {"title": "Search All Memory Types", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query text"}, "limit": {"type": "integer", "default": 5, "description": "Max results per memory type"}}, "required": ["query"]}},
                {"name": "timeline", "description": "Search memory by time range. Use for 'what happened last week' or 'when did I...' questions.",
                 "annotations": {"title": "Timeline Search", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"after": {"type": "string", "description": "Start of range, ISO datetime (e.g. 2025-02-01T00:00:00Z)"}, "before": {"type": "string", "description": "End of range, ISO datetime"}}}},
                {"name": "vault_stats", "description": "Get memory vault statistics — entity count, fact count, knowledge breakdown.",
                 "annotations": {"title": "Vault Statistics", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "get_entity", "description": "Get full details of a specific entity — all facts, relations, and knowledge artifacts.",
                 "annotations": {"title": "Get Entity", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Entity name to look up"}}, "required": ["name"]}},
                {"name": "delete_entity", "description": "Permanently delete an entity and all its data (facts, relations, knowledge, embeddings).",
                 "annotations": {"title": "Delete Entity", "readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Entity name to delete"}}, "required": ["name"]}},
                {"name": "list_episodes", "description": "List or search episodic memories — events, interactions, decisions with timestamps and outcomes.",
                 "annotations": {"title": "List Episodes", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Optional search query to filter episodes"}, "limit": {"type": "integer", "default": 20, "description": "Maximum episodes to return"}}}},
                {"name": "list_procedures", "description": "List learned workflows/procedures with steps, success/fail counts, and version history.",
                 "annotations": {"title": "List Procedures", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Optional search query to filter procedures"}, "limit": {"type": "integer", "default": 10, "description": "Maximum procedures to return"}}}},
                {"name": "procedure_feedback", "description": "Record success or failure for a procedure. On failure with context, automatically evolves the procedure to a new version.",
                 "annotations": {"title": "Procedure Feedback", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"procedure_id": {"type": "string", "description": "UUID of the procedure"}, "success": {"type": "boolean", "description": "True if workflow succeeded, false if failed"}, "context": {"type": "string", "description": "What went wrong — required for failure to trigger evolution"}, "failed_at_step": {"type": "integer", "description": "Which step number failed (optional)"}}, "required": ["procedure_id", "success"]}},
                {"name": "procedure_history", "description": "Show how a procedure evolved over time — all versions, diffs, and evolution triggers.",
                 "annotations": {"title": "Procedure History", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"procedure_id": {"type": "string", "description": "UUID of any version of the procedure"}}, "required": ["procedure_id"]}},
                {"name": "run_agents", "description": "Run AI memory agents: curator (clean contradictions), connector (find patterns), digest (weekly summary), or all.",
                 "annotations": {"title": "Run Memory Agents", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"agent": {"type": "string", "enum": ["curator", "connector", "digest", "all"], "description": "Which agent to run"}, "auto_fix": {"type": "boolean", "default": True, "description": "Auto-archive low quality facts (curator only)"}}}},
                {"name": "get_insights", "description": "Get AI-generated insights from memory analysis — patterns, connections, and reflections.",
                 "annotations": {"title": "Get Insights", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "get_graph", "description": "Get the full knowledge graph — all entities as nodes and their relationships as edges.",
                 "annotations": {"title": "Knowledge Graph", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "get_triggers", "description": "List smart triggers — pending reminders, detected contradictions, and discovered patterns.",
                 "annotations": {"title": "Smart Triggers", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"include_fired": {"type": "boolean", "default": False, "description": "Include already-fired triggers"}}}},
                {"name": "get_feed", "description": "Get activity feed — recent memory changes, new entities, updated facts, and events.",
                 "annotations": {"title": "Activity Feed", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20, "description": "Maximum feed items to return"}}}},
                {"name": "archive_fact", "description": "Archive a specific fact on an entity — soft-delete without removing the entity itself.",
                 "annotations": {"title": "Archive Fact", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"entity_name": {"type": "string", "description": "Entity the fact belongs to"}, "fact_content": {"type": "string", "description": "Exact text of the fact to archive"}}, "required": ["entity_name", "fact_content"]}},
                {"name": "merge_entities", "description": "Merge two entities into one — combines all facts, relations, and knowledge into the target entity.",
                 "annotations": {"title": "Merge Entities", "readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"source": {"type": "string", "description": "Entity to merge FROM (will be deleted)"}, "target": {"type": "string", "description": "Entity to merge INTO (will be kept)"}}, "required": ["source", "target"]}},
                {"name": "reflect", "description": "Trigger AI reflection on all memories — analyzes facts to find patterns, insights, and hidden connections.",
                 "annotations": {"title": "Reflect", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "dismiss_trigger", "description": "Dismiss a smart trigger without firing its webhook.",
                 "annotations": {"title": "Dismiss Trigger", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"trigger_id": {"type": "integer", "description": "ID of the trigger to dismiss"}}, "required": ["trigger_id"]}},
                {"name": "fix_entity_type", "description": "Fix an entity's type classification to any descriptive type.",
                 "annotations": {"title": "Fix Entity Type", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Entity name to reclassify"}, "new_type": {"type": "string", "description": "Correct entity type (e.g. person, project, technology, company, concept, place, activity, event, book, tool, etc.)"}}, "required": ["name", "new_type"]}},
                {"name": "list_memories", "description": "List all stored memory entities with their types and fact counts.",
                 "annotations": {"title": "List Memories", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "get_reflections", "description": "Get AI-generated reflections — insights and patterns found across memories. Optional scope: entity, cross, temporal.",
                 "annotations": {"title": "Get Reflections", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"scope": {"type": "string", "enum": ["entity", "cross", "temporal"], "description": "Filter reflections by scope"}}}},
                {"name": "dedup", "description": "Find and automatically merge duplicate entities.",
                 "annotations": {"title": "Deduplicate", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "checkpoint", "description": "Save a session checkpoint with decisions, learnings, and next steps.",
                 "annotations": {"title": "Checkpoint", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"summary": {"type": "string", "description": "Brief summary of what was accomplished"}, "decisions": {"type": "array", "items": {"type": "string"}, "description": "Key decisions made"}, "learnings": {"type": "array", "items": {"type": "string"}, "description": "Things learned"}, "next_steps": {"type": "array", "items": {"type": "string"}, "description": "What needs to happen next"}}, "required": ["summary"]}},
                {"name": "context_for", "description": "Get relevant memory context for a specific task — entities, procedures, and past events.",
                 "annotations": {"title": "Context For Task", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"task": {"type": "string", "description": "Description of the task"}}, "required": ["task"]}},
                {"name": "generate_rules_file", "description": "Generate a CLAUDE.md, .cursorrules, or .windsurfrules file from memory.",
                 "annotations": {"title": "Generate Rules File", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"format": {"type": "string", "enum": ["claude_md", "cursorrules", "windsurf"], "description": "Output format"}}}},
            ],
            "resources": [
                {"uri": "memory://profile", "name": "Cognitive Profile", "description": "LLM-generated user profile from all memory types — pin for instant personalization.", "mimeType": "text/markdown"},
                {"uri": "memory://procedures", "name": "Active Procedures", "description": "Learned workflows with steps, trigger conditions, and reliability stats.", "mimeType": "text/markdown"},
                {"uri": "memory://triggers", "name": "Pending Triggers", "description": "Smart triggers: reminders, contradictions, and patterns detected in memory.", "mimeType": "text/markdown"},
                {"uri": "memory://recent", "name": "Recently Saved", "description": "Last 5 facts saved — check before saving to avoid duplicates.", "mimeType": "text/markdown"},
            ],
        }

    try:
        from mcp.server.sse import SseServerTransport
        from api.cloud_mcp_server import create_cloud_mcp_server as _create_mcp, DIRECTORY_CONNECTOR_TOOLS
        from cloud.client import CloudMemory as _CloudMemory
        from starlette.responses import JSONResponse as _JSONResponse

        _mcp_sse = SseServerTransport("/mcp/messages/")

        def _extract_mcp_key(request: Request) -> str:
            """Extract API key from Authorization header, apiKey header, or query param."""
            # 1. Standard Authorization: Bearer om-...
            auth = request.headers.get("authorization", "")
            if auth:
                return auth.replace("Bearer ", "").strip()
            # 2. Smithery-style apiKey header
            api_key = request.headers.get("apikey", "")
            if api_key:
                return api_key.strip()
            # 3. Query param fallback
            return request.query_params.get("apiKey", "").strip()

        async def _handle_mcp_sse(request: Request):
            """SSE endpoint — clients connect here first."""
            key = _extract_mcp_key(request)
            if not key:
                return _JSONResponse({"error": "Missing API key"}, status_code=401)
            uid = store.verify_api_key(key)
            if not uid:
                return _JSONResponse({"error": "Invalid API key"}, status_code=401)

            # Bypass Cloudflare by calling our own REST API via localhost —
            # server-to-self HTTP through mengram.io triggers CF error 1010 (Browser Integrity Check).
            base = os.environ.get("MENGRAM_INTERNAL_URL") \
                or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"
            mem = _CloudMemory(api_key=key, base_url=base)
            mcp_server = _create_mcp(mem)

            async with _mcp_sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await mcp_server.run(
                    streams[0], streams[1],
                    mcp_server.create_initialization_options()
                )

        async def _handle_mcp_messages(request: Request):
            """POST endpoint — clients send MCP messages here."""
            await _mcp_sse.handle_post_message(
                request.scope, request.receive, request._send
            )

        app.add_route("/mcp/sse", _handle_mcp_sse)
        app.add_route("/mcp/messages/", _handle_mcp_messages, methods=["POST"])
        logger.info("✅ MCP HTTP (SSE) transport enabled at /mcp/sse")

        # ---- Streamable HTTP transport (MCP 2025-03-26 spec) ----
        try:
            from mcp.server.streamable_http import StreamableHTTPServerTransport
            import anyio

            class _MCPStreamableHandler:
                """ASGI handler for MCP streamable HTTP.

                Registered as a class instance (not a function) so Starlette
                skips the request_response() wrapper that expects a Response
                return value.  transport.handle_request() writes the response
                directly via the ASGI ``send`` callable and returns None —
                a function endpoint would cause TypeError after every request.

                ``tool_filter`` (optional) restricts the served tools to a
                curated subset — used for the slim Connectors Directory surface.
                """

                def __init__(self, tool_filter=None):
                    self._tool_filter = tool_filter

                async def __call__(self, scope, receive, send):
                    request = Request(scope, receive, send)
                    # Point unauthenticated MCP clients (Claude Connectors) at the
                    # OAuth flow via RFC 9728 resource metadata. API-key clients
                    # (CLI/Cursor config) simply send the key and never see this.
                    _www = {"WWW-Authenticate":
                            'Bearer resource_metadata="https://mengram.io/.well-known/oauth-protected-resource"'}
                    key = _extract_mcp_key(request)
                    if not key:
                        resp = _JSONResponse({"error": "Missing API key"}, status_code=401, headers=_www)
                        await resp(scope, receive, send)
                        return
                    uid = store.verify_api_key(key)
                    if not uid:
                        resp = _JSONResponse({"error": "Invalid API key"}, status_code=401, headers=_www)
                        await resp(scope, receive, send)
                        return

                    # Bypass Cloudflare by calling our own REST API via localhost —
                    # server-to-self HTTP through mengram.io triggers CF error 1010 (Browser Integrity Check).
                    base = os.environ.get("MENGRAM_INTERNAL_URL") \
                        or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"
                    mem = _CloudMemory(api_key=key, base_url=base)
                    mcp_server = _create_mcp(mem, tool_filter=self._tool_filter)

                    transport = StreamableHTTPServerTransport(
                        mcp_session_id=None,
                        is_json_response_enabled=True,
                    )

                    async with transport.connect() as (read_stream, write_stream):
                        async with anyio.create_task_group() as tg:
                            async def _run():
                                await mcp_server.run(
                                    read_stream, write_stream,
                                    mcp_server.create_initialization_options(),
                                    stateless=True,
                                )
                            tg.start_soon(_run)
                            await transport.handle_request(scope, receive, send)

            # No GET: the transport is stateless (mcp_session_id=None), so the
            # server never emits server-initiated messages and a GET SSE stream
            # would hang until the edge kills it at 900s, pinning one of the two
            # gunicorn workers until the --timeout 300 watchdog SIGABRTs it.
            # The spec allows 405 here when a server offers no standalone stream.
            app.add_route("/mcp", _MCPStreamableHandler(), methods=["POST", "DELETE"])
            logger.info("✅ MCP Streamable HTTP transport enabled at /mcp")

            # Slim, curated surface for the Claude Connectors Directory listing —
            # same handler, restricted to the DIRECTORY_CONNECTOR_TOOLS subset.
            app.add_route("/mcp/connector", _MCPStreamableHandler(DIRECTORY_CONNECTOR_TOOLS),
                          methods=["POST", "DELETE"])
            logger.info("✅ MCP Connectors Directory surface at /mcp/connector (%d tools)",
                        len(DIRECTORY_CONNECTOR_TOOLS))

        except ImportError:
            logger.info("ℹ️  MCP Streamable HTTP not available (mcp>=1.26 required)")

    except ImportError:
        logger.info("ℹ️  MCP SSE transport not available (mcp package not installed)")

    return app


# ---- Module-level app for gunicorn ----
# gunicorn cloud.api:app -w 4 -k uvicorn.workers.UvicornWorker
app = create_cloud_api()


# ---- Entry point (local dev) ----

def main():
    import uvicorn
    port = int(os.environ.get("PORT", 8420))

    logger.info(f"🧠 Mengram Cloud API")
    logger.info(f"   http://0.0.0.0:{port}")
    logger.info(f"   Docs: https://docs.mengram.io")
    logger.info(f"   Swagger: http://localhost:{port}/swagger")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
