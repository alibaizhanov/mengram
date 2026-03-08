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
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("mengram")

from fastapi import FastAPI, HTTPException, Depends, Header, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse, RedirectResponse
from dataclasses import dataclass
from pydantic import BaseModel

from cloud.store import CloudStore


# ---- Auth Context ----

@dataclass
class AuthContext:
    """Auth result with plan info for quota enforcement."""
    user_id: str
    plan: str         # free, pro, business
    rate_limit: int   # per-minute rate limit

PLAN_QUOTAS = {
    "free":     {"adds": 100,   "searches": 500,    "agents": 5,   "reflects": 5,   "dedups": 2,   "reindexes": 2,   "rules": 5,    "rate_limit": 30,  "webhooks": 0,  "teams": 0,  "sub_users": 3},
    "pro":      {"adds": 1_000, "searches": 10_000, "agents": 50,  "reflects": 30,  "dedups": 20,  "reindexes": 10,  "rules": 50,   "rate_limit": 120, "webhooks": 10, "teams": 5,  "sub_users": 50},
    "business": {"adds": 5_000, "searches": 30_000, "agents": -1,  "reflects": -1,  "dedups": -1,  "reindexes": -1,  "rules": -1,   "rate_limit": 300, "webhooks": 50, "teams": -1, "sub_users": -1},
}


# ---- Config ----

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://localhost:5432/mengram"
)
REDIS_URL = os.environ.get("REDIS_PUBLIC_URL") or os.environ.get("REDIS_URL")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Mengram <onboarding@resend.dev>")

# ---- Models ----

class Message(BaseModel):
    role: str
    content: str

class AddRequest(BaseModel):
    messages: list[Message]
    user_id: str = "default"
    agent_id: str | None = None
    run_id: str | None = None
    app_id: str | None = None
    expiration_date: str | None = None

class AddTextRequest(BaseModel):
    text: str
    user_id: str = "default"
    agent_id: str | None = None
    run_id: str | None = None
    app_id: str | None = None
    expiration_date: str | None = None

class SearchRequest(BaseModel):
    query: str
    user_id: str = "default"
    agent_id: str | None = None
    run_id: str | None = None
    app_id: str | None = None
    limit: int = 5
    graph_depth: int = 2  # 0=no graph, 1=1-hop, 2=2-hop (default)
    filters: dict | None = None  # metadata filters, e.g. {"agent_id": "support-bot"}

class FeedbackRequest(BaseModel):
    context: str | None = None         # What went wrong (triggers evolution on failure)
    failed_at_step: int | None = None  # Which step failed

import re
import ipaddress
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

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

class SignupRequest(BaseModel):
    email: str

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
        version="2.16.0",
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
            return response

    app.add_middleware(RateLimitHeaderMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    )

    store = CloudStore(DATABASE_URL, pool_min=2, pool_max=10, redis_url=REDIS_URL)

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
            openai_key = os.environ.get("OPENAI_API_KEY", "")
            if openai_key:
                from cloud.embedder import CloudEmbedder
                _embedder = CloudEmbedder(provider="openai", api_key=openai_key)
        return _embedder

    # ---- Re-ranking (Cohere Rerank → LLM fallback) ----
    _cohere_client = None
    _openai_rerank_client = None

    def rerank_results(query: str, results: list[dict], plan: str = "business") -> list[dict]:
        """Re-rank search results based on subscription plan.
        Free: no reranking.  Pro: LLM rerank.  Business: Cohere Rerank → LLM fallback."""
        if not results or len(results) <= 1:
            return results

        # Free plan: no reranking — return raw vector results
        if plan == "free":
            return results

        # Try Cohere Rerank first — fact-level (cross-encoder, more precise)
        # Only for Business plan (Pro skips straight to LLM rerank)
        cohere_key = os.environ.get("COHERE_API_KEY", "") if plan == "business" else ""
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
                resp = co.rerank(
                    model="rerank-v3.5",
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

                # Rebuild results: only entities with relevant facts, facts reordered
                reranked = []
                for eidx in sorted(entity_facts.keys()):
                    r = dict(results[eidx])
                    scored_facts = sorted(entity_facts[eidx], key=lambda x: x[1], reverse=True)
                    r["facts"] = [f[0] for f in scored_facts[:7]]
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
                facts_str = "; ".join(r.get("facts", [])[:5])
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
                model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=100,
                temperature=1,
            )

            text = resp.choices[0].message.content.strip()
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
            logger.error(f"⚠️ Re-ranking failed, returning raw results: {e}")
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
                    logger.info(f"🚫 BLOCKED {action} | user={ctx.user_id[:8]} | plan={ctx.plan} | {cached}/{max_allowed} (cached)")
                    _raise_quota_error(action, max_allowed, int(cached), ctx.plan, ctx.user_id)
        except Exception:
            pass  # Redis down → fall through to DB

        # Step 2: Atomic check-and-increment in PostgreSQL
        try:
            store.check_and_increment(ctx.user_id, action, max_allowed, count)
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
                new_count = store.get_usage_count(ctx.user_id, action)
                redis_client.set(cache_key, str(new_count), ex=_quota_month_end_ttl())
        except Exception:
            pass  # Redis down → counter will be set on next request

    def _raise_quota_error(action, max_allowed, current, plan, user_id=None):
        if user_id:
            logger.warning(f"🚫 QUOTA {action} | user={user_id[:8]} | {current}/{max_allowed} | plan={plan}")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "quota_exceeded",
                "action": action,
                "limit": max_allowed,
                "used": current,
                "plan": plan,
                "upgrade_url": "https://mengram.io/#pricing",
                "message": f"Monthly {action} limit reached ({max_allowed}). Upgrade your plan at https://mengram.io/#pricing",
            }
        )

    # ---- Auth middleware ----

    async def auth(request: Request, authorization: str = Header(...)) -> AuthContext:
        """Verify API key, return AuthContext with plan info. Rate limited per plan."""
        key = authorization.replace("Bearer ", "")
        user_id = store.verify_api_key(key)
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Look up subscription (cached 5 min)
        sub = store.get_subscription(user_id)
        plan = sub.get("plan", "free") if sub else "free"
        if plan not in PLAN_QUOTAS:
            plan = "free"
        rate_limit = PLAN_QUOTAS[plan]["rate_limit"]

        if not _check_rate_limit(user_id, rate_limit):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({rate_limit} requests/min). Retry in 60 seconds.",
                headers={
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

        key_prefix = key[:10] if len(key) > 10 else key[:4]
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
                    <svg width="36" height="36" viewBox="0 0 120 120"><path d="M60 16 Q92 16 96 48 Q100 78 72 88 Q50 96 38 76 Q26 58 46 46 Q62 38 70 52 Q76 64 62 68" fill="none" stroke="#a855f7" stroke-width="8" stroke-linecap="round"/><circle cx="62" cy="68" r="8" fill="#a855f7"/><circle cx="62" cy="68" r="3.5" fill="white"/></svg>
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
                    <a href="https://mengram.io/dashboard" style="color:#7c3aed;text-decoration:none">Console</a> ·
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
                    <p style="font-size:15px;color:#e8e8f0;margin:0;line-height:1.6">You explain the same things to AI every day. Your stack. Your bugs. Your preferences. <strong style="color:#a855f7">Mengram remembers so you don't have to.</strong></p>
                </div>

                <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:20px 0;text-align:center">
                    <p style="color:#8888a8;font-size:12px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px">Your API Key</p>
                    <code style="font-size:14px;color:#a78bfa;word-break:break-all">{api_key}</code>
                </div>
                <p style="font-size:13px;color:#ef4444;font-weight:600">Save this key — it won't be shown again.</p>

                <div style="margin:28px 0">
                    <p style="font-size:15px;font-weight:600;color:#e8e8f0;margin:0 0 12px">Get started:</p>

                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:16px;margin:0 0 10px">
                        <code style="color:#22c55e;font-size:13px">pip install mengram-ai</code>
                    </div>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:16px">
                        <code style="color:#22c55e;font-size:13px">mengram setup --key {api_key}</code>
                    </div>
                    <p style="color:#8888a8;font-size:12px;margin:8px 0 0">Saves your key to shell profile and installs Claude Code hooks. Done.</p>
                </div>

                <div style="margin:28px 0">
                    <p style="font-size:14px;font-weight:600;color:#e8e8f0;margin:0 0 10px">What you can do:</p>
                    <p style="font-size:13px;color:#c8c8d8;margin:0;line-height:2">
                        <span style="color:#a855f7">→</span> <strong>Claude Code</strong> — auto-save, auto-recall, profile on every session<br>
                        <span style="color:#a855f7">→</span> <strong>Python / JS SDK</strong> — <code style="color:#a78bfa">m.search("tech stack")</code>, <code style="color:#a78bfa">m.add([...])</code><br>
                        <span style="color:#a855f7">→</span> <strong>MCP Server</strong> — Cursor, Windsurf, Claude Desktop<br>
                        <span style="color:#a855f7">→</span> <strong>3 memory types</strong> — facts, events, workflows that evolve<br>
                        <span style="color:#a855f7">→</span> <strong>Cognitive Profile</strong> — one call → system prompt for any LLM<br>
                        <span style="color:#a855f7">→</span> <strong>Import ChatGPT</strong> — <code style="color:#a78bfa">mengram import chatgpt export.zip --cloud</code>
                    </p>
                </div>

                <hr style="border:none;border-top:1px solid #1a1a2e;margin:28px 0">
                <p style="font-size:14px;color:#c8c8d8;margin:0 0 16px">Something not working? Just reply — I read every email.</p>
                <p style="font-size:14px;color:#c8c8d8;margin:0 0 20px">— Ali</p>
                <p style="font-size:12px;color:#55556a;text-align:center">
                    <a href="https://docs.mengram.io" style="color:#7c3aed;text-decoration:none">Docs</a> ·
                    <a href="https://mengram.io/dashboard" style="color:#7c3aed;text-decoration:none">Dashboard</a> ·
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

    def _send_verification_email(email: str, code: str):
        """Send 6-digit verification code via Resend."""
        resend_key = os.environ.get("RESEND_API_KEY")
        if not resend_key:
            logger.warning("⚠️  RESEND_API_KEY not set, cannot send verification code")
            return
        try:
            import resend
            resend.api_key = resend_key
            resend.Emails.send({
                "from": EMAIL_FROM,
                "to": [email],
                "subject": f"Mengram verification code: {code}",
                "html": f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
                    <h2 style="color:#a855f7;">Mengram</h2>
                    <p>Your verification code:</p>
                    <div style="background:#f5f5f7;padding:16px 24px;border-radius:8px;text-align:center;margin:16px 0;">
                        <span style="font-size:32px;font-weight:700;letter-spacing:8px;color:#1a1a2e;">{code}</span>
                    </div>
                    <p style="color:#666;font-size:14px;">This code expires in 10 minutes.</p>
                </div>
                """,
            })
            logger.info(f"📧 Verification code sent to {email}")
        except Exception as e:
            logger.error(f"⚠️  Verification email failed: {e}")

    def _send_drip_email(email: str, drip_type: str, code: str = None):
        """Send an onboarding drip email."""
        resend_key = os.environ.get("RESEND_API_KEY")
        if not resend_key:
            return
        try:
            import resend
            resend.api_key = resend_key

            # Common email wrapper
            def _wrap(subject: str, body_html: str):
                return f"""
                <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:520px;margin:0 auto;padding:40px 24px;color:#e8e8f0;background:#0a0a12;border-radius:16px">
                    <div style="text-align:center;margin-bottom:32px">
                        <svg width="36" height="36" viewBox="0 0 120 120"><path d="M60 16 Q92 16 96 48 Q100 78 72 88 Q50 96 38 76 Q26 58 46 46 Q62 38 70 52 Q76 64 62 68" fill="none" stroke="#a855f7" stroke-width="8" stroke-linecap="round"/><circle cx="62" cy="68" r="8" fill="#a855f7"/><circle cx="62" cy="68" r="3.5" fill="white"/></svg>
                        <h1 style="font-size:22px;font-weight:700;margin:8px 0 4px;color:#e8e8f0">Mengram</h1>
                    </div>
                    {body_html}
                    <hr style="border:none;border-top:1px solid #1a1a2e;margin:28px 0">
                    <p style="font-size:12px;color:#55556a;text-align:center">
                        <a href="https://mengram.io/dashboard" style="color:#7c3aed;text-decoration:none">Console</a> &middot;
                        <a href="https://docs.mengram.io" style="color:#7c3aed;text-decoration:none">Docs</a> &middot;
                        <a href="https://github.com/alibaizhanov/mengram" style="color:#7c3aed;text-decoration:none">GitHub</a>
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
                    <div style="text-align:center;margin:24px 0">
                        <a href="https://docs.mengram.io/quickstart" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Read quickstart guide</a>
                    </div>"""

            elif drip_type == "completed_72h":
                subject = "3 ways to use Mengram"
                body = """
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Haven't tried Mengram yet? Here are 3 popular ways to get started:</p>
                    <div style="margin:20px 0">
                        <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                            <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">1. MCP Server — works with Claude, Cursor, Windsurf</p>
                            <p style="color:#8888a8;font-size:13px;margin:0">Add memory to any AI tool with zero code.</p>
                        </div>
                        <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                            <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">2. Python / JavaScript SDK</p>
                            <p style="color:#8888a8;font-size:13px;margin:0">Build apps with persistent AI memory in a few lines.</p>
                        </div>
                        <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:12px 0">
                            <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">3. n8n / REST API</p>
                            <p style="color:#8888a8;font-size:13px;margin:0">Automate memory with workflows or direct API calls.</p>
                        </div>
                    </div>
                    <div style="text-align:center;margin:24px 0">
                        <a href="https://docs.mengram.io" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Explore docs</a>
                    </div>"""

            elif drip_type == "completed_7d":
                subject = "Your memory vault is empty"
                body = """
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">Your Mengram vault is still empty. Add your first memory and see the magic:</p>
                    <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:20px 0">
                        <p style="color:#a78bfa;font-weight:600;margin:0 0 8px">Try it right now</p>
                        <pre style="margin:0;font-size:13px;color:#22c55e;white-space:pre-wrap"><code>curl -X POST https://mengram.io/v1/add \\
  -H "Authorization: Bearer YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"messages":[{"role":"user","content":"I like coffee"}]}'</code></pre>
                    </div>
                    <div style="text-align:center;margin:24px 0">
                        <a href="https://mengram.io/dashboard" style="background:#7c3aed;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Open dashboard</a>
                    </div>"""

            elif drip_type == "incomplete_1h":
                subject = "Your verification code is waiting"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">You started signing up for Mengram but haven't verified your email yet. Here's a fresh code:</p>
                    <div style="background:#f5f5f7;padding:16px 24px;border-radius:8px;text-align:center;margin:20px 0">
                        <span style="font-size:32px;font-weight:700;letter-spacing:8px;color:#1a1a2e">{code}</span>
                    </div>
                    <p style="color:#8888a8;font-size:14px">This code expires in 10 minutes. Enter it at <a href="https://mengram.io/dashboard" style="color:#7c3aed">mengram.io/dashboard</a>.</p>"""

            elif drip_type == "incomplete_24h":
                subject = "Still want to try Mengram?"
                body = f"""
                    <p style="font-size:15px;color:#c8c8d8;line-height:1.6">You signed up for Mengram yesterday but never finished verification. Here's one last code:</p>
                    <div style="background:#f5f5f7;padding:16px 24px;border-radius:8px;text-align:center;margin:20px 0">
                        <span style="font-size:32px;font-weight:700;letter-spacing:8px;color:#1a1a2e">{code}</span>
                    </div>
                    <p style="color:#8888a8;font-size:14px">This code expires in 10 minutes. Enter it at <a href="https://mengram.io/dashboard" style="color:#7c3aed">mengram.io/dashboard</a>.</p>
                    <p style="color:#55556a;font-size:12px;margin-top:16px">This is the last reminder we'll send.</p>"""
            else:
                return

            html = _wrap(subject, body)
            resend.Emails.send({
                "from": EMAIL_FROM,
                "to": [email],
                "subject": subject,
                "html": html,
            })
            logger.info(f"📧 Drip email '{drip_type}' sent to {email}")
        except Exception as e:
            logger.error(f"⚠️ Drip email '{drip_type}' failed for {email}: {e}")

    # ---- Public endpoints ----

    @app.get("/", response_class=HTMLResponse)
    async def landing():
        """Landing page."""
        landing_path = Path(__file__).parent / "landing.html"
        return landing_path.read_text(encoding="utf-8")

    @app.get("/pricing", response_class=HTMLResponse)
    async def pricing():
        """Pricing page — renders landing with scroll to pricing section."""
        landing_path = Path(__file__).parent / "landing.html"
        html = landing_path.read_text(encoding="utf-8")
        # Inject auto-scroll to pricing section
        html = html.replace("</body>", '<script>document.getElementById("pricing")?.scrollIntoView()</script></body>')
        return html

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots():
        return "User-agent: *\nAllow: /\nSitemap: https://mengram.io/sitemap.xml"

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

    @app.get("/sitemap.xml")
    async def sitemap():
        """XML sitemap for search engines."""
        from starlette.responses import Response
        # (url, priority, changefreq)
        pages = [
            # Core — highest priority
            ("https://mengram.io", "1.0", "weekly"),
            ("https://mengram.io/pricing", "0.8", "monthly"),
            # Claude Code — high SEO value
            ("https://mengram.io/vs/claude-mem", "0.9", "weekly"),
            # VS comparison — high SEO value
            ("https://mengram.io/vs/mem0", "0.9", "weekly"),
            ("https://mengram.io/vs/zep", "0.8", "weekly"),
            ("https://mengram.io/vs/letta", "0.8", "weekly"),
            ("https://mengram.io/vs/langmem", "0.8", "weekly"),
            ("https://mengram.io/vs/supermemory", "0.8", "weekly"),
            # Blog — high SEO value
            ("https://mengram.io/blog", "0.8", "weekly"),
            ("https://mengram.io/blog/what-is-ai-memory", "0.8", "monthly"),
            ("https://mengram.io/blog/ai-memory-vs-rag", "0.8", "monthly"),
            ("https://mengram.io/blog/semantic-episodic-procedural-memory", "0.8", "monthly"),
            ("https://mengram.io/blog/how-to-add-memory-to-ai-agents", "0.8", "monthly"),
            ("https://mengram.io/blog/cognitive-profile-system-prompts", "0.7", "monthly"),
            ("https://mengram.io/blog/mcp-memory-server-setup", "0.8", "monthly"),
            ("https://mengram.io/blog/mem0-vs-mengram-benchmark", "0.8", "monthly"),
            ("https://mengram.io/blog/ai-memory-for-crewai-langchain", "0.7", "monthly"),
            ("https://mengram.io/blog/claude-code-memory-hooks", "0.9", "weekly"),
            # Use cases
            ("https://mengram.io/usecase/customer-support", "0.7", "monthly"),
            ("https://mengram.io/usecase/personal-assistant", "0.7", "monthly"),
            ("https://mengram.io/usecase/education", "0.6", "monthly"),
            ("https://mengram.io/usecase/healthcare", "0.6", "monthly"),
            ("https://mengram.io/usecase/sales", "0.7", "monthly"),
            # Legal
            ("https://mengram.io/terms", "0.3", "yearly"),
            ("https://mengram.io/privacy", "0.3", "yearly"),
            ("https://mengram.io/refund", "0.3", "yearly"),
        ]
        today = "2026-03-05"
        entries = "\n".join(
            f"  <url>\n"
            f"    <loc>{url}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>{freq}</changefreq>\n"
            f"    <priority>{prio}</priority>\n"
            f"  </url>"
            for url, prio, freq in pages
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{entries}\n"
            "</urlset>"
        )
        return Response(content=xml, media_type="application/xml")

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        """Memory Console."""
        dashboard_path = Path(__file__).parent / "dashboard.html"
        return dashboard_path.read_text(encoding="utf-8")

    @app.get("/terms", response_class=HTMLResponse)
    async def terms():
        """Terms of Service."""
        p = Path(__file__).parent / "terms.html"
        return p.read_text(encoding="utf-8")

    @app.get("/privacy", response_class=HTMLResponse)
    async def privacy():
        """Privacy Policy."""
        p = Path(__file__).parent / "privacy.html"
        return p.read_text(encoding="utf-8")

    @app.get("/refund", response_class=HTMLResponse)
    async def refund():
        """Refund Policy."""
        p = Path(__file__).parent / "refund.html"
        return p.read_text(encoding="utf-8")

    # ---- VS / Comparison pages (SEO) ----
    VS_PAGES = {
        "mem0": {
            "slug": "mem0",
            "name": "Mem0",
            "tagline": "Both store memories. Only Mengram learns workflows.",
            "description": "Mem0 is a popular fact-storage tool with 25K+ GitHub stars. Mengram adds episodic memory, procedural memory that evolves from failures, and Cognitive Profile.",
            "their_good": [
                "25K+ GitHub stars — largest community",
                "Well-funded ($24M, YC S24)",
                "Solid fact retrieval (graph + vector + KV)",
                "Python &amp; JS SDKs with good docs",
            ],
            "their_missing": [
                "No episodic memory (events, decisions)",
                "No procedural memory (workflows)",
                "No self-improving workflows",
                "No Cognitive Profile",
                "No unified search across memory types",
                "$19–249/mo paid tiers",
            ],
            "has_semantic": "&#x2705;",
            "has_episodic": "&#x274C;",
            "has_multiuser": "&#x2705;",
            "has_graph": "&#x2705;",
            "has_mcp": "&#x2705;",
            "has_selfhost": "&#x2705;",
            "their_price": "$19–249/mo",
            "best_for_them": "Reliable fact storage with the largest community. Great if you only need to remember user preferences and personal details.",
            "best_for_us": "Agents that learn from experience — remember facts AND events AND workflows. Free cloud API with 3 memory types, Cognitive Profile, and MCP.",
            "website": "https://mem0.ai",
            "seo_title": "Mengram vs Mem0 — AI Memory Comparison (2026)",
            "seo_description": "Compare Mengram and Mem0 for AI agent memory. Mengram adds episodic memory, procedural memory that evolves from failures, and Cognitive Profile. Free alternative to Mem0.",
            "seo_keywords": "Mem0 alternative, Mengram vs Mem0, AI memory comparison, mem0ai alternative, best AI memory tool",
        },
        "zep": {
            "slug": "zep",
            "name": "Zep",
            "tagline": "Zep tracks time. Mengram learns from experience.",
            "description": "Zep is an enterprise AI memory tool with temporal knowledge graph and SOC2/HIPAA compliance. Mengram offers 3 memory types, procedural learning, and a free cloud API.",
            "their_good": [
                "Temporal knowledge graph — tracks how facts change over time",
                "SOC2 and HIPAA compliance",
                "Sub-200ms latency targets",
                "Python, TypeScript, and Go SDKs",
            ],
            "their_missing": [
                "Cloud-only (community edition deprecated)",
                "Enterprise pricing only — no free tier",
                "No episodic memory",
                "No procedural memory",
                "No self-improving workflows",
                "No Cognitive Profile",
            ],
            "has_semantic": "&#x2705;",
            "has_episodic": "&#x274C;",
            "has_multiuser": "&#x2705;",
            "has_graph": "&#x2705;",
            "has_mcp": "&#x274C;",
            "has_selfhost": "&#x274C;",
            "their_price": "Enterprise",
            "best_for_them": "Enterprise apps in regulated industries (healthcare, finance) where SOC2/HIPAA and temporal reasoning are requirements.",
            "best_for_us": "Agents that learn and improve over time. 3 memory types, free cloud API, self-hostable, MCP + LangChain + CrewAI integrations.",
            "website": "https://www.getzep.com",
            "seo_title": "Mengram vs Zep — AI Memory Comparison (2026)",
            "seo_description": "Compare Mengram and Zep for AI agent memory. Mengram offers 3 memory types and procedural learning. Free open-source alternative to Zep's enterprise-only pricing.",
            "seo_keywords": "Zep alternative, Mengram vs Zep, AI memory comparison, getzep alternative, free AI memory API",
        },
        "letta": {
            "slug": "letta",
            "name": "Letta",
            "tagline": "Letta lets agents self-curate. Mengram gives them 3 memory types.",
            "description": "Letta (formerly MemGPT) pioneered agent-controlled memory from UC Berkeley research. Mengram takes a different approach with 3 structured memory types and procedural learning.",
            "their_good": [
                "Novel agent-controlled memory architecture",
                "UC Berkeley research-backed (MemGPT paper)",
                "Free and self-hostable",
                "Great for long-running conversations",
            ],
            "their_missing": [
                "No procedural memory",
                "Only partial episodic memory (conversation archival)",
                "No self-improving workflows",
                "No Cognitive Profile",
                "Agent memory management adds unpredictability",
                "Limited managed hosting options",
            ],
            "has_semantic": "&#x2705;",
            "has_episodic": "Partial",
            "has_multiuser": "&#x274C;",
            "has_graph": "&#x274C;",
            "has_mcp": "&#x2705;",
            "has_selfhost": "&#x2705;",
            "their_price": "Free (self-host)",
            "best_for_them": "Long-running conversational agents where the agent should organically manage its own context and memory.",
            "best_for_us": "Structured memory with 3 types that the developer controls. Procedures evolve from failures. Free cloud API + MCP + framework integrations.",
            "website": "https://www.letta.com",
            "seo_title": "Mengram vs Letta (MemGPT) — AI Memory Comparison (2026)",
            "seo_description": "Compare Mengram and Letta (MemGPT) for AI agent memory. Mengram offers semantic + episodic + procedural memory with self-improving workflows. Free alternative.",
            "seo_keywords": "Letta alternative, MemGPT alternative, Mengram vs Letta, AI memory comparison, best AI memory tool 2026",
        },
        "langmem": {
            "slug": "langmem",
            "name": "LangMem",
            "tagline": "LangMem extends LangGraph. Mengram works with everything.",
            "description": "LangMem is LangChain's memory module for LangGraph agents. Mengram is a standalone memory API that works with any framework — LangChain, CrewAI, OpenAI, or direct API calls.",
            "their_good": [
                "Native LangGraph integration — first-class LangChain support",
                "Thread-scoped and cross-thread memory",
                "Backed by LangChain team — strong ecosystem alignment",
                "Memory formed via background processing",
            ],
            "their_missing": [
                "Tightly coupled to LangGraph — harder to use outside LangChain",
                "No episodic memory (events, decisions)",
                "No procedural memory (workflows)",
                "No Cognitive Profile",
                "No standalone MCP server",
                "No knowledge graph visualization",
            ],
            "has_semantic": "&#x2705;",
            "has_episodic": "&#x274C;",
            "has_multiuser": "&#x2705;",
            "has_graph": "&#x274C;",
            "has_mcp": "&#x274C;",
            "has_selfhost": "&#x2705;",
            "their_price": "Via LangSmith plans",
            "best_for_them": "Teams already using LangGraph and LangSmith who want memory deeply integrated into their LangChain workflow.",
            "best_for_us": "Framework-agnostic memory with 3 types. Works with any LLM, any framework, any client. Free cloud API + MCP + Cognitive Profile.",
            "website": "https://langchain-ai.github.io/long-term-memory/",
            "seo_title": "Mengram vs LangMem — AI Memory Comparison (2026)",
            "seo_description": "Compare Mengram and LangMem for AI agent memory. Mengram offers 3 memory types, Cognitive Profile, and framework-agnostic API. Works beyond LangChain.",
            "seo_keywords": "LangMem alternative, Mengram vs LangMem, LangChain memory alternative, AI memory comparison, LangGraph memory",
        },
        "supermemory": {
            "slug": "supermemory",
            "name": "Supermemory",
            "tagline": "Supermemory bookmarks the web. Mengram remembers conversations.",
            "description": "Supermemory is a personal knowledge manager that saves and searches bookmarks, tweets, and web content. Mengram is an AI memory API that extracts and stores memories from conversations.",
            "their_good": [
                "Great browser extension for saving web content",
                "ChatGPT-style interface for querying saved content",
                "Good for personal knowledge management",
                "Open source and self-hostable",
            ],
            "their_missing": [
                "Not designed for AI agent memory — it's a bookmark tool",
                "No conversation memory extraction",
                "No episodic memory",
                "No procedural memory",
                "No Cognitive Profile",
                "No multi-user isolation for agent use cases",
                "No MCP server",
            ],
            "has_semantic": "Partial",
            "has_episodic": "&#x274C;",
            "has_multiuser": "&#x274C;",
            "has_graph": "&#x274C;",
            "has_mcp": "&#x274C;",
            "has_selfhost": "&#x2705;",
            "their_price": "Free (self-host)",
            "best_for_them": "Personal knowledge management — saving bookmarks, tweets, and web articles for later retrieval and search.",
            "best_for_us": "AI agent memory that learns from conversations. 3 memory types, Cognitive Profile, MCP server, and multi-user isolation for production apps.",
            "website": "https://supermemory.ai",
            "seo_title": "Mengram vs Supermemory — AI Memory Comparison (2026)",
            "seo_description": "Compare Mengram and Supermemory. Supermemory is a bookmark manager. Mengram is an AI memory API with semantic, episodic, and procedural memory for agents.",
            "seo_keywords": "Supermemory alternative, Mengram vs Supermemory, AI memory comparison, best AI memory 2026, Supermemory vs Mengram",
        },
        "claude-mem": {
            "slug": "claude-mem",
            "name": "claude-mem",
            "tagline": "claude-mem saves conversations. Mengram builds a full memory loop.",
            "description": "claude-mem is a simple Claude Code hook that saves conversations to Markdown files. Mengram provides a complete memory system: auto-save, auto-recall on every prompt, cognitive profile on session start, and 3 memory types.",
            "their_good": [
                "Simple and easy to understand",
                "Works as a Claude Code hook",
                "Saves conversations to local Markdown files",
                "No API key or account needed",
            ],
            "their_missing": [
                "No auto-recall — saves but never retrieves",
                "No cognitive profile loading on session start",
                "No semantic search across memories",
                "No episodic or procedural memory",
                "No cross-tool support (only Claude Code)",
                "No MCP server, no LangChain/CrewAI integration",
                "No knowledge graph or entity extraction",
                "Local-only — no cloud sync or multi-device",
            ],
            "has_semantic": "&#x274C;",
            "has_episodic": "&#x274C;",
            "has_multiuser": "&#x274C;",
            "has_graph": "&#x274C;",
            "has_mcp": "&#x274C;",
            "has_selfhost": "&#x2705;",
            "their_price": "Free (local)",
            "best_for_them": "Simple conversation logging to local files. Good if you just want a text record of past Claude Code sessions.",
            "best_for_us": "Full memory loop — auto-save, auto-recall, cognitive profile. Claude knows who you are, what you worked on, and brings relevant context on every prompt. Works across Claude Code, MCP, LangChain, CrewAI, and REST API.",
            "website": "https://github.com/anthropics/claude-code",
            "seo_title": "Mengram vs claude-mem — Claude Code Memory Comparison (2026)",
            "seo_description": "Compare Mengram and claude-mem for Claude Code memory. claude-mem saves conversations. Mengram adds auto-recall, cognitive profile, 3 memory types, and cross-tool support. Free alternative.",
            "seo_keywords": "claude-mem alternative, Mengram vs claude-mem, Claude Code memory, Claude Code persistent memory, Claude Code hooks memory, best Claude Code memory tool, claude code auto save, claude code auto recall",
        },
    }

    @app.get("/vs/{competitor}", response_class=HTMLResponse)
    async def vs_page(competitor: str):
        """SEO comparison page: Mengram vs competitor."""
        # MemGPT redirects to Letta (rebranded)
        if competitor == "memgpt":
            from starlette.responses import RedirectResponse
            return RedirectResponse(url="/vs/letta", status_code=301)
        data = VS_PAGES.get(competitor)
        if not data:
            raise HTTPException(404, "Comparison page not found")
        template_path = Path(__file__).parent / "vs.html"
        html = template_path.read_text(encoding="utf-8")
        data["their_good_html"] = "".join(f"<li>{x}</li>" for x in data["their_good"])
        data["their_missing_html"] = "".join(f"<li>{x}</li>" for x in data["their_missing"])
        return html.format(**data)

    # ---- Blog posts (SEO content) ----
    BLOG_POSTS = {
        "what-is-ai-memory": {
            "slug": "what-is-ai-memory",
            "title": "What is AI Memory? A Developer's Guide to Persistent Memory for LLMs",
            "date": "February 20, 2026",
            "date_iso": "2026-02-20",
            "read_time": "7",
            "tags": ["Guide", "Fundamentals"],
            "excerpt": "Learn what AI memory is, why LLMs need it, and how persistent memory transforms stateless chatbots into context-aware agents.",
            "seo_title": "What is AI Memory? A Developer's Guide to Persistent Memory for LLMs",
            "seo_description": "Learn what AI memory is, why LLMs need it, and how persistent memory with semantic, episodic, and procedural types transforms AI agents. Developer guide with code examples.",
            "seo_keywords": "what is AI memory, AI memory explained, LLM memory, persistent memory for AI, AI agent memory",
            "content_html": """
<h2>Why LLMs forget everything</h2>
<p>Large language models like GPT-4, Claude, and Gemini are stateless by default. Every conversation starts from scratch. Ask the same question twice, and the model has no idea you asked before. This is a fundamental limitation — the <strong>context window is temporary storage</strong>, not memory.</p>
<p>Context windows have grown (128K+ tokens), but they still reset between sessions. RAG (Retrieval-Augmented Generation) helps by fetching relevant documents, but it only retrieves static information — it doesn't learn from interactions.</p>

<h2>What is AI memory?</h2>
<p><strong>AI memory</strong> is a persistent storage layer that lets LLMs and AI agents remember information across conversations. Instead of resetting every session, AI memory continuously extracts, stores, and retrieves knowledge from past interactions.</p>
<p>Think of it like the difference between a goldfish and a human. Without memory, every conversation is new. With memory, your AI builds a cumulative understanding of users, projects, and context over time.</p>

<h2>Three types of AI memory</h2>
<p>Human memory isn't one thing — it's three distinct systems. The most effective AI memory systems mirror this structure:</p>

<h3>1. Semantic memory (facts)</h3>
<p>What the user knows, prefers, and believes. Examples: "User prefers Python over JavaScript", "User is a senior engineer at Acme Corp", "User is allergic to peanuts."</p>
<p>Most AI memory tools only implement this type. <a href="/vs/mem0">Mem0</a>, for instance, is primarily a semantic memory store.</p>

<h3>2. Episodic memory (events)</h3>
<p>What happened, when, and in what context. Examples: "User debugged a Redis connection error on Feb 12", "User decided to migrate from AWS to GCP last week."</p>
<p>Episodic memory captures the narrative of interactions — not just facts, but the <em>story</em> of what happened.</p>

<h3>3. Procedural memory (workflows)</h3>
<p>How to do things, step by step. Examples: "When deploying, run tests first, then build, then push to staging." Procedural memory captures learned workflows that evolve from experience.</p>
<p>This is the rarest type — <a href="/blog/semantic-episodic-procedural-memory">learn more about all three types</a>.</p>

<h2>How AI memory works in practice</h2>
<p>Here's how you add AI memory to any LLM application with Mengram:</p>

<pre><code>from mengram import Mengram

m = Mengram(api_key="your-key")

# After each conversation, add to memory
m.add("I prefer dark mode and use VS Code", user_id="alice")

# Before generating a response, search memory
results = m.search("What IDE does Alice use?", user_id="alice")

# Or generate a full Cognitive Profile
profile = m.profile(user_id="alice")
# Returns a ready-to-use system prompt with everything known about Alice</code></pre>

<p>The <code>profile()</code> call is unique to Mengram — it generates a complete system prompt from all stored memories, making any LLM instantly personalized. <a href="/blog/cognitive-profile-system-prompts">Read more about Cognitive Profile</a>.</p>

<h2>AI memory vs RAG</h2>
<p>RAG and AI memory solve different problems. RAG retrieves from static document collections. AI memory learns from dynamic conversations. You often need both — <a href="/blog/ai-memory-vs-rag">read our detailed comparison</a>.</p>

<h2>Getting started</h2>
<p>The fastest way to add AI memory to your application:</p>
<pre><code>pip install mengram-ai</code></pre>
<p>Get a free API key at <a href="/#signup">mengram.io</a> and start building. Works with any LLM — OpenAI, Anthropic, Google, open-source models. Also available as an <a href="/blog/mcp-memory-server-setup">MCP server for Claude Desktop</a>.</p>
""",
            "related": ["ai-memory-vs-rag", "semantic-episodic-procedural-memory"],
        },
        "ai-memory-vs-rag": {
            "slug": "ai-memory-vs-rag",
            "title": "AI Memory vs RAG: Why Context Windows Aren't Enough",
            "date": "February 18, 2026",
            "date_iso": "2026-02-18",
            "read_time": "6",
            "tags": ["Comparison", "Architecture"],
            "excerpt": "RAG retrieves documents. AI memory learns from interactions. Understand when to use each and why the best agents use both.",
            "seo_title": "AI Memory vs RAG: Why Context Windows Aren't Enough | Mengram",
            "seo_description": "Compare AI memory and RAG (Retrieval-Augmented Generation). Learn why context windows aren't enough, when to use each approach, and how to combine them for smarter AI agents.",
            "seo_keywords": "AI memory vs RAG, RAG alternative, context window limitations, persistent AI memory, retrieval augmented generation vs memory",
            "content_html": """
<h2>The context window problem</h2>
<p>Every LLM has a context window — a fixed-size buffer that holds the current conversation plus any injected context. When the window fills up, old messages get dropped. When the session ends, everything is lost.</p>
<p>Developers have tried two approaches to solve this: <strong>RAG</strong> (Retrieval-Augmented Generation) and <strong>AI memory</strong>. They're complementary but fundamentally different.</p>

<h2>How RAG works</h2>
<p>RAG retrieves relevant documents from a static knowledge base and injects them into the prompt:</p>
<pre><code># Traditional RAG pipeline
chunks = vector_db.search("How to deploy?", top_k=5)
context = "\\n".join([c.text for c in chunks])
prompt = f"Context: {{context}}\\n\\nQuestion: How to deploy?"
response = llm.generate(prompt)</code></pre>
<p><strong>RAG is great for:</strong> Documentation search, knowledge bases, FAQ bots, question-answering over static documents.</p>
<p><strong>RAG falls short when:</strong> You need to remember past interactions, learn user preferences, or track decisions made across sessions.</p>

<h2>How AI memory works</h2>
<p>AI memory <em>learns from conversations</em> and builds a cumulative understanding over time:</p>
<pre><code># AI memory with Mengram
from mengram import Mengram
m = Mengram(api_key="key")

# Each conversation enriches the memory
m.add("User prefers concise answers with code examples", user_id="bob")
m.add("Bob debugged CORS issue on staging server today", user_id="bob")

# Next session: the AI knows Bob's history
profile = m.profile(user_id="bob")
# "Bob is a developer who prefers concise answers with code examples.
#  Recently debugged a CORS issue on staging..."</code></pre>

<h2>Key differences</h2>
<p><strong>Source of truth:</strong> RAG draws from documents you upload. AI memory draws from conversations that happen naturally.</p>
<p><strong>Static vs dynamic:</strong> RAG knowledge is fixed until you re-index. AI memory continuously evolves with every interaction.</p>
<p><strong>What vs who:</strong> RAG answers "what does the documentation say?" AI memory answers "what does this user need?"</p>
<p><strong>Types:</strong> RAG stores chunks of text. AI memory stores structured knowledge — <a href="/blog/semantic-episodic-procedural-memory">facts (semantic), events (episodic), and workflows (procedural)</a>.</p>

<h2>When to use both</h2>
<p>The best AI agents combine RAG and memory. RAG provides domain knowledge. Memory provides user context. Together, you get an agent that knows your product <em>and</em> knows your user.</p>
<pre><code># Combine RAG + AI memory
docs = rag.search(user_query)
memories = mengram.search(user_query, user_id=user_id)
profile = mengram.profile(user_id=user_id)

prompt = f\"\"\"System: {{profile}}
Relevant docs: {{docs}}
User memories: {{memories}}
Question: {{user_query}}\"\"\"</code></pre>

<h2>Getting started</h2>
<p>Replace your pure-RAG setup with Mengram in 3 lines: <code>pip install mengram-ai</code>, get a <a href="/#signup">free API key</a>, and call <code>m.add()</code> after each conversation. Your AI will start learning from every interaction.</p>
""",
            "related": ["what-is-ai-memory", "how-to-add-memory-to-ai-agents"],
        },
        "semantic-episodic-procedural-memory": {
            "slug": "semantic-episodic-procedural-memory",
            "title": "3 Types of AI Memory: Semantic, Episodic & Procedural Explained",
            "date": "February 15, 2026",
            "date_iso": "2026-02-15",
            "read_time": "8",
            "tags": ["Deep Dive", "Fundamentals"],
            "excerpt": "Understand the three types of memory that make AI agents truly intelligent: semantic (facts), episodic (events), and procedural (workflows).",
            "seo_title": "3 Types of AI Memory: Semantic, Episodic & Procedural Explained",
            "seo_description": "Deep dive into the 3 types of AI memory: semantic (facts), episodic (events), and procedural (workflows). Learn how each type works and why agents need all three.",
            "seo_keywords": "types of AI memory, semantic memory AI, episodic memory AI, procedural memory AI, AI agent memory types, memory-augmented LLMs",
            "content_html": """
<h2>Why one type of memory isn't enough</h2>
<p>Most AI memory tools store only facts — "user likes Python", "user lives in San Francisco." This is semantic memory, and it's useful but incomplete. Humans don't just remember facts. We remember <em>experiences</em> and <em>skills</em> too.</p>
<p>Mengram implements all three types of human memory for AI agents. Here's how each works and why it matters.</p>

<h2>Semantic memory: facts and knowledge</h2>
<p>Semantic memory stores <strong>what the AI knows</strong> about a user, project, or domain. It's context-free — the facts exist independent of when or how they were learned.</p>
<pre><code># Semantic memories extracted automatically:
"User prefers TypeScript over JavaScript"
"User works at Acme Corp as a senior engineer"
"User's project uses PostgreSQL with pgvector"
"User prefers dark mode in all tools"</code></pre>
<p>This is the baseline. Tools like <a href="/vs/mem0">Mem0</a> and <a href="/vs/zep">Zep</a> implement semantic memory well. But it's only the foundation.</p>

<h2>Episodic memory: events and experiences</h2>
<p>Episodic memory stores <strong>what happened</strong> — specific events, decisions, and interactions with full context: when, where, and why.</p>
<pre><code># Episodic memories:
"On Feb 12, user spent 2 hours debugging a Redis connection timeout.
 Root cause was pool_max=2 under concurrent load. Fixed by increasing to 5."

"On Feb 10, user decided to migrate from REST to GraphQL
 after discovering N+1 query problems in the dashboard API."

"On Feb 8, user paired with Sarah on the auth refactor.
 They chose JWT over sessions for stateless scaling."</code></pre>
<p>Episodic memory enables the AI to reference past events: "Last time you had a Redis issue, it was a pool size problem — want me to check that first?" This is the difference between a tool and a colleague.</p>

<h2>Procedural memory: workflows and skills</h2>
<p>Procedural memory stores <strong>how to do things</strong> — step-by-step workflows that the AI learns from observing the user's patterns.</p>
<pre><code># Procedural memories:
"Deploy workflow: run tests → build Docker image → push to staging →
 smoke test → promote to production → notify #eng-deploys"

"Code review process: check for security issues first →
 verify test coverage → review naming conventions →
 suggest performance improvements last"

"Bug triage: reproduce locally → check error logs →
 identify affected users → create ticket → assign priority"</code></pre>
<p>The critical feature of procedural memory is that it <strong>evolves from failures</strong>. When a deployment fails because the user forgot to run migrations, Mengram updates the procedure to include that step. The AI gets better over time.</p>

<h2>How all three work together</h2>
<p>Consider a customer support agent with all three memory types:</p>
<ul>
<li><strong>Semantic:</strong> "This customer is on the Pro plan, uses the React SDK, and prefers email over chat."</li>
<li><strong>Episodic:</strong> "Last week, this customer reported a billing issue that was resolved by applying a promo code."</li>
<li><strong>Procedural:</strong> "For billing issues: check subscription status → verify payment method → check for failed charges → escalate to billing team if unresolved."</li>
</ul>
<p>With all three, the agent doesn't just have facts — it has <em>experience</em> and <em>skills</em>. It knows the customer, remembers their history, and follows a proven resolution workflow.</p>

<h2>Using all three types with Mengram</h2>
<pre><code>from mengram import Mengram
m = Mengram(api_key="key")

# Add any conversation — Mengram auto-extracts all 3 types
m.add("Deployed to staging, but migrations failed. Had to rollback, run migrations manually, then redeploy.", user_id="alice")

# Search across all types
m.search("deployment process", user_id="alice")

# Cognitive Profile merges all types into one system prompt
profile = m.profile(user_id="alice")
</code></pre>
<p>Mengram automatically classifies and extracts all three memory types from natural conversation. No manual tagging required. <a href="/blog/how-to-add-memory-to-ai-agents">Get started in 5 minutes</a>.</p>
""",
            "related": ["what-is-ai-memory", "cognitive-profile-system-prompts"],
        },
        "how-to-add-memory-to-ai-agents": {
            "slug": "how-to-add-memory-to-ai-agents",
            "title": "How to Add Memory to AI Agents in 5 Minutes (Python & JS)",
            "date": "February 12, 2026",
            "date_iso": "2026-02-12",
            "read_time": "5",
            "tags": ["Tutorial", "Quick Start"],
            "excerpt": "Step-by-step tutorial to add persistent memory to any AI agent using Python or JavaScript. Works with OpenAI, Anthropic, and any LLM.",
            "seo_title": "How to Add Memory to AI Agents in 5 Minutes (Python & JS) | Mengram",
            "seo_description": "Step-by-step tutorial: add persistent memory to AI agents in Python or JavaScript. Works with OpenAI, Anthropic, and any LLM. Free API, 5-minute setup.",
            "seo_keywords": "add memory to AI agents, AI agent memory tutorial, Python AI memory, JavaScript AI memory, persistent memory for LLMs, Mengram tutorial",
            "content_html": """
<h2>Prerequisites</h2>
<ul>
<li>Python 3.8+ or Node.js 18+</li>
<li>A free Mengram API key — <a href="/#signup">get one here</a></li>
<li>Any LLM API (OpenAI, Anthropic, etc.) or a local model</li>
</ul>

<h2>Step 1: Install</h2>

<h3>Python</h3>
<pre><code>pip install mengram-ai</code></pre>

<h3>JavaScript</h3>
<pre><code>npm install mengram</code></pre>

<h2>Step 2: Initialize</h2>

<h3>Python</h3>
<pre><code>from mengram import Mengram

m = Mengram(api_key="mg-...")  # or set MENGRAM_API_KEY env var</code></pre>

<h3>JavaScript</h3>
<pre><code>import Mengram from 'mengram';

const m = new Mengram({{ apiKey: 'mg-...' }});</code></pre>

<h2>Step 3: Store memories after each conversation</h2>
<p>After your agent finishes a conversation turn, pass the exchange to Mengram. It automatically extracts <a href="/blog/semantic-episodic-procedural-memory">all three memory types</a>.</p>

<h3>Python</h3>
<pre><code># Store the conversation — Mengram extracts facts, events, and workflows
m.add(
    "User asked how to deploy to production. I walked them through "
    "the CI/CD pipeline: push to main, GitHub Actions runs tests, "
    "builds Docker image, deploys to staging, then promotes to prod.",
    user_id="user-123"
)</code></pre>

<h3>JavaScript</h3>
<pre><code>await m.add(
  "User asked how to deploy to production. I walked them through " +
  "the CI/CD pipeline: push to main, GitHub Actions runs tests, " +
  "builds Docker image, deploys to staging, then promotes to prod.",
  {{ userId: 'user-123' }}
);</code></pre>

<h2>Step 4: Search memories before responding</h2>
<pre><code># Python
results = m.search("deployment process", user_id="user-123")
for r in results:
    print(r.memory, r.type, r.score)</code></pre>

<pre><code>// JavaScript
const results = await m.search('deployment process', {{ userId: 'user-123' }});
results.forEach(r => console.log(r.memory, r.type, r.score));</code></pre>

<h2>Step 5: Use Cognitive Profile for instant personalization</h2>
<p>Instead of searching for specific memories, generate a complete system prompt:</p>
<pre><code># Python — one API call returns a ready-to-use system prompt
profile = m.profile(user_id="user-123")
print(profile)
# "You are assisting user-123, a developer who works with CI/CD pipelines..."

# Use it with any LLM
response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[
        {{"role": "system", "content": profile}},
        {{"role": "user", "content": user_message}}
    ]
)</code></pre>
<p><a href="/blog/cognitive-profile-system-prompts">Learn more about Cognitive Profile</a>.</p>

<h2>Full example: OpenAI agent with memory</h2>
<pre><code>from openai import OpenAI
from mengram import Mengram

openai = OpenAI()
m = Mengram()

def chat(user_id: str, message: str) -> str:
    # Get personalized system prompt from memory
    profile = m.profile(user_id=user_id)

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {{"role": "system", "content": profile}},
            {{"role": "user", "content": message}}
        ]
    )
    reply = response.choices[0].message.content

    # Store the exchange in memory
    m.add(f"User: {{message}}\\nAssistant: {{reply}}", user_id=user_id)
    return reply</code></pre>

<p>That's it. Your agent now remembers every conversation and gets smarter over time. Also works with <a href="/blog/ai-memory-for-crewai-langchain">CrewAI and LangChain</a>, or as an <a href="/blog/mcp-memory-server-setup">MCP server for Claude Desktop</a>.</p>
""",
            "related": ["cognitive-profile-system-prompts", "mcp-memory-server-setup"],
        },
        "cognitive-profile-system-prompts": {
            "slug": "cognitive-profile-system-prompts",
            "title": "Cognitive Profile: Auto-Generate System Prompts from User Memory",
            "date": "February 10, 2026",
            "date_iso": "2026-02-10",
            "read_time": "6",
            "tags": ["Feature", "Deep Dive"],
            "excerpt": "Cognitive Profile generates a complete system prompt from a user's memory history. One API call turns scattered memories into a personalized context block.",
            "seo_title": "Cognitive Profile: Auto-Generate System Prompts from User Memory | Mengram",
            "seo_description": "Learn how Cognitive Profile auto-generates system prompts from stored AI memory. One API call turns user facts, events, and workflows into a personalized context block for any LLM.",
            "seo_keywords": "cognitive profile AI, auto generate system prompt, AI personalization, system prompt from memory, Mengram cognitive profile, LLM personalization",
            "content_html": """
<h2>The system prompt problem</h2>
<p>Every personalized AI application faces the same challenge: how do you build a system prompt that captures everything the AI should know about a user?</p>
<p>Most developers manually craft system prompts or stitch together search results. This is fragile, incomplete, and doesn't scale. As you accumulate hundreds or thousands of memories per user, you can't fit them all in a prompt.</p>

<h2>What is Cognitive Profile?</h2>
<p><strong>Cognitive Profile</strong> is a Mengram feature that generates a complete, ready-to-use system prompt from a user's entire memory history. One API call distills all semantic memories (facts), episodic memories (events), and procedural memories (workflows) into a coherent personality snapshot.</p>

<pre><code>from mengram import Mengram
m = Mengram(api_key="mg-...")

# One call — returns a complete system prompt
profile = m.profile(user_id="alice")</code></pre>

<p>The output looks like this:</p>
<pre><code># Example Cognitive Profile output:
"You are assisting Alice, a senior backend engineer at Acme Corp.

Key facts:
- Prefers Python, uses FastAPI and PostgreSQL
- Works on the payments team
- Prefers concise answers with code examples

Recent context:
- Debugged a Redis connection timeout last week (pool size issue)
- Currently migrating the auth system from sessions to JWT
- Deployed v2.3 to production yesterday with zero downtime

Learned workflows:
- Deploy process: run tests → build → push staging → smoke test → promote
- Code review: security first → test coverage → naming → performance
- When Alice asks about deployment, reference the established workflow above."</code></pre>

<h2>How it works internally</h2>
<ol>
<li><strong>Retrieval:</strong> Fetches all memory types for the user (semantic, episodic, procedural)</li>
<li><strong>Ranking:</strong> Prioritizes recent and frequently-accessed memories</li>
<li><strong>Synthesis:</strong> An LLM compresses and organizes the memories into a structured prompt</li>
<li><strong>Caching:</strong> The profile is cached and incrementally updated as new memories arrive</li>
</ol>

<h2>Why not just use search?</h2>
<p><code>search()</code> returns individual memories matching a query. It's great for specific questions. But for <em>general context</em> — "who is this user and what should I know about them?" — search requires you to guess the right queries.</p>
<p>Cognitive Profile answers the general question automatically. Use <code>search()</code> for specific retrieval and <code>profile()</code> for global context. They're complementary.</p>

<h2>Using Cognitive Profile with any LLM</h2>
<pre><code># Works with OpenAI
import openai
profile = m.profile(user_id="alice")
response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[
        {{"role": "system", "content": profile}},
        {{"role": "user", "content": "How should I deploy the new feature?"}}
    ]
)

# Works with Anthropic
import anthropic
client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    system=profile,
    messages=[{{"role": "user", "content": "How should I deploy?"}}]
)

# Works with any LLM that accepts a system prompt</code></pre>

<h2>When to use Cognitive Profile</h2>
<ul>
<li><strong>Chatbots and assistants:</strong> Start every conversation with full user context</li>
<li><strong>Customer support:</strong> Agents instantly know the customer's history and preferences</li>
<li><strong>Personal AI:</strong> Build companions that truly know the user</li>
<li><strong>Multi-agent systems:</strong> Share user context across agents without manual prompt engineering</li>
</ul>

<p>Get started: <code>pip install mengram-ai</code>, grab a <a href="/#signup">free API key</a>, and call <code>m.profile(user_id)</code>. <a href="/blog/how-to-add-memory-to-ai-agents">Full quickstart tutorial here</a>.</p>
""",
            "related": ["how-to-add-memory-to-ai-agents", "semantic-episodic-procedural-memory"],
        },
        "mcp-memory-server-setup": {
            "slug": "mcp-memory-server-setup",
            "title": "Set Up an AI Memory MCP Server for Claude Desktop",
            "date": "February 8, 2026",
            "date_iso": "2026-02-08",
            "read_time": "5",
            "tags": ["Tutorial", "MCP"],
            "excerpt": "Connect Mengram's AI memory to Claude Desktop via MCP. 29 tools for search, add, profile, and more — setup in under 3 minutes.",
            "seo_title": "Set Up an AI Memory MCP Server for Claude Desktop | Mengram",
            "seo_description": "Step-by-step guide to set up Mengram's MCP server for Claude Desktop. 29 memory tools including search, add, profile, knowledge graph, and smart triggers.",
            "seo_keywords": "MCP memory server, Claude Desktop memory, MCP server setup, AI memory MCP, Model Context Protocol memory, Claude Desktop persistent memory",
            "content_html": """
<h2>What is MCP?</h2>
<p>The <strong>Model Context Protocol (MCP)</strong> is an open standard that lets AI applications like Claude Desktop, Cursor, and Windsurf connect to external tools and data sources. An MCP server provides tools that the AI can call during conversations.</p>
<p>Mengram's MCP server gives Claude Desktop 29 memory tools — search, add, profile, knowledge graph, triggers, dedup, reflections, and more — turning it into an AI that remembers everything across sessions.</p>

<h2>Installation</h2>
<p>You need a Mengram API key (<a href="/#signup">get one free</a>) and Claude Desktop installed.</p>

<h3>Option 1: npx (recommended)</h3>
<p>Add this to your Claude Desktop config file (<code>claude_desktop_config.json</code>):</p>
<pre><code>{{
  "mcpServers": {{
    "mengram": {{
      "command": "npx",
      "args": ["-y", "mengram"],
      "env": {{
        "MENGRAM_API_KEY": "mg-your-api-key"
      }}
    }}
  }}
}}</code></pre>

<h3>Option 2: pip</h3>
<pre><code>pip install mengram-ai</code></pre>
<pre><code>{{
  "mcpServers": {{
    "mengram": {{
      "command": "python",
      "args": ["-m", "mengram", "mcp"],
      "env": {{
        "MENGRAM_API_KEY": "mg-your-api-key"
      }}
    }}
  }}
}}</code></pre>

<h2>Available tools (12 total)</h2>
<p>Once connected, Claude Desktop gains these tools:</p>
<ul>
<li><strong>memory_add</strong> — Store new memories from the conversation</li>
<li><strong>memory_search</strong> — Search across all memory types with semantic matching</li>
<li><strong>memory_profile</strong> — Generate a <a href="/blog/cognitive-profile-system-prompts">Cognitive Profile</a> system prompt</li>
<li><strong>memory_list</strong> — List all memories for a user</li>
<li><strong>memory_delete</strong> — Remove specific memories</li>
<li><strong>memory_graph</strong> — Query the knowledge graph for entity relationships</li>
<li><strong>memory_triggers</strong> — Set up smart triggers that fire on memory events</li>
<li><strong>memory_import</strong> — Import from ChatGPT exports, Obsidian vaults, or text files</li>
<li><strong>memory_export</strong> — Export all memories as JSON</li>
<li><strong>memory_stats</strong> — View memory usage statistics</li>
<li><strong>memory_reflect</strong> — Trigger AI reflection on stored memories</li>
<li><strong>memory_deduplicate</strong> — Clean up duplicate or conflicting memories</li>
</ul>

<h2>How Claude uses memory</h2>
<p>After setup, Claude Desktop automatically:</p>
<ol>
<li>Searches your memory at the start of conversations for relevant context</li>
<li>Stores important information from your conversations</li>
<li>Uses your Cognitive Profile to personalize responses</li>
<li>Builds a knowledge graph of entities and relationships from your interactions</li>
</ol>

<h2>Example conversation</h2>
<pre><code>You: "Remember that I prefer using Railway for deployments and my project uses FastAPI"

Claude: I've stored that in your memory. Next time you ask about deployment,
I'll know you use Railway with FastAPI.

--- (next session) ---

You: "How should I set up CI/CD?"

Claude: Since you use Railway with FastAPI, here's how I'd set up your CI/CD...
[Uses memory context to give a personalized answer]</code></pre>

<h2>Also works with</h2>
<p>The same MCP server works with Cursor, Windsurf, VS Code Copilot, and any other MCP-compatible client. The configuration is the same — just add the <code>mengram</code> server to your MCP config.</p>

<p><a href="/blog/how-to-add-memory-to-ai-agents">Also available as a Python/JS SDK</a> for custom integrations.</p>
""",
            "related": ["how-to-add-memory-to-ai-agents", "what-is-ai-memory"],
        },
        "mem0-vs-mengram-benchmark": {
            "slug": "mem0-vs-mengram-benchmark",
            "title": "Mem0 vs Mengram: Feature Comparison & Benchmark (2026)",
            "date": "February 5, 2026",
            "date_iso": "2026-02-05",
            "read_time": "7",
            "tags": ["Comparison", "Benchmark"],
            "excerpt": "Detailed feature-by-feature comparison of Mem0 and Mengram for AI agent memory. Pricing, memory types, API design, and performance benchmarks.",
            "seo_title": "Mem0 vs Mengram: Feature Comparison & Benchmark (2026)",
            "seo_description": "Detailed comparison of Mem0 vs Mengram for AI memory. Compare memory types, pricing, API design, MCP support, and performance. Free Mem0 alternative with 3 memory types.",
            "seo_keywords": "Mem0 vs Mengram, Mem0 alternative, best AI memory tool 2026, Mem0 comparison, AI memory benchmark, free Mem0 alternative",
            "content_html": """
<h2>Overview</h2>
<p><a href="/vs/mem0">Mem0</a> and Mengram are both AI memory solutions, but they take fundamentally different approaches. Mem0 focuses on semantic fact storage with a large community. Mengram adds episodic and procedural memory types plus Cognitive Profile.</p>

<h2>Feature comparison</h2>

<table style="width:100%; border-collapse:collapse; font-size:14px; margin:20px 0;">
<thead>
<tr style="border-bottom:1px solid #1a1a2e;">
<th style="padding:10px; text-align:left; color:#9898b0;">Feature</th>
<th style="padding:10px; text-align:center; color:#a855f7; font-weight:600;">Mengram</th>
<th style="padding:10px; text-align:center; color:#9898b0;">Mem0</th>
</tr>
</thead>
<tbody>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Semantic memory (facts)</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x2705;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e; background:rgba(168,85,247,0.05);"><td style="padding:10px;font-weight:600;">Episodic memory (events)</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x274C;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e; background:rgba(168,85,247,0.05);"><td style="padding:10px;font-weight:600;">Procedural memory (workflows)</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x274C;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e; background:rgba(168,85,247,0.05);"><td style="padding:10px;font-weight:600;">Self-improving procedures</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x274C;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e; background:rgba(168,85,247,0.05);"><td style="padding:10px;font-weight:600;">Cognitive Profile</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x274C;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Knowledge graph</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x2705;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Multi-user isolation</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x2705;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">MCP server</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x2705;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Self-hostable</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x2705;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Open source</td><td style="text-align:center;">MIT</td><td style="text-align:center;">Apache 2.0</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Free tier</td><td style="text-align:center; color:#34d399; font-weight:600;">100 adds / 500 searches</td><td style="text-align:center;">10K memories</td></tr>
<tr><td style="padding:10px;">Paid plans</td><td style="text-align:center;">$19–99/mo</td><td style="text-align:center;">$19–249/mo</td></tr>
</tbody>
</table>

<h2>Memory types: the key difference</h2>
<p>Mem0 stores facts (semantic memory) and has recently added graph memory for entity relationships. It does this well with a mature SDK and large community (40K+ GitHub stars).</p>
<p>Mengram stores <a href="/blog/semantic-episodic-procedural-memory">three distinct types</a>: semantic (facts), episodic (events with context), and procedural (workflows that evolve). This means Mengram agents don't just remember <em>what</em> you told them — they remember <em>what happened</em> and <em>how to do things</em>.</p>

<h2>Cognitive Profile</h2>
<p>Mengram's unique feature is <a href="/blog/cognitive-profile-system-prompts">Cognitive Profile</a> — one API call generates a complete system prompt from a user's entire memory. Mem0 requires you to manually search and assemble context.</p>

<h2>API comparison</h2>
<pre><code># Mengram
from mengram import Mengram
m = Mengram(api_key="key")
m.add("conversation text", user_id="u1")
results = m.search("query", user_id="u1")
profile = m.profile(user_id="u1")  # unique to Mengram</code></pre>

<pre><code># Mem0
from mem0 import MemoryClient
client = MemoryClient(api_key="key")
client.add("conversation text", user_id="u1")
results = client.search("query", user_id="u1")
# No equivalent to profile()</code></pre>

<h2>When to choose Mem0</h2>
<p>Mem0 is a strong choice if you need: the largest community and ecosystem, SOC2-compliant enterprise deployment, graph-based fact storage, or are already invested in their tooling.</p>

<h2>When to choose Mengram</h2>
<p>Mengram is better if you need: episodic and procedural memory, self-improving workflows, Cognitive Profile for instant personalization, or a free tier with all features. See the <a href="/vs/mem0">full comparison page</a>.</p>
""",
            "related": ["what-is-ai-memory", "ai-memory-vs-rag"],
        },
        "ai-memory-for-crewai-langchain": {
            "slug": "ai-memory-for-crewai-langchain",
            "title": "Add Persistent Memory to CrewAI & LangChain Agents",
            "date": "February 2, 2026",
            "date_iso": "2026-02-02",
            "read_time": "6",
            "tags": ["Tutorial", "Integration"],
            "excerpt": "Add long-term memory to CrewAI and LangChain agents with Mengram. Code examples for both frameworks with semantic, episodic, and procedural memory.",
            "seo_title": "Add Persistent Memory to CrewAI & LangChain Agents | Mengram",
            "seo_description": "Tutorial: add persistent AI memory to CrewAI and LangChain agents. Code examples for semantic, episodic, and procedural memory integration. Works with any LLM.",
            "seo_keywords": "CrewAI memory, LangChain memory, persistent memory CrewAI, LangChain persistent memory, AI agent memory integration, CrewAI Mengram",
            "content_html": """
<h2>Why agent frameworks need external memory</h2>
<p>CrewAI and LangChain are excellent frameworks for building multi-agent systems. But their built-in memory is limited to the current session. When the script ends, everything is forgotten.</p>
<p>Adding Mengram gives your agents persistent <a href="/blog/semantic-episodic-procedural-memory">semantic, episodic, and procedural memory</a> that survives across sessions and improves over time.</p>

<h2>CrewAI integration</h2>
<p>CrewAI has native Mengram support via the <code>mengram</code> extra:</p>
<pre><code>pip install 'crewai[mengram]'</code></pre>

<p>Configure in your crew:</p>
<pre><code>from crewai import Crew, Agent, Task

# Set your Mengram API key
import os
os.environ["MENGRAM_API_KEY"] = "mg-your-key"

researcher = Agent(
    role="Senior Researcher",
    goal="Find relevant information on the topic",
    backstory="You are an experienced researcher.",
    memory=True  # Enables CrewAI's memory system
)

crew = Crew(
    agents=[researcher],
    tasks=[...],
    memory=True,
    memory_config={{
        "provider": "mengram",
    }}
)

result = crew.kickoff()
# Memories persist across crew runs!</code></pre>

<h2>LangChain integration</h2>
<p>Use Mengram as a memory backend for LangChain agents:</p>
<pre><code>from langchain_openai import ChatOpenAI
from mengram import Mengram

llm = ChatOpenAI(model="gpt-4o")
m = Mengram(api_key="mg-your-key")

def agent_with_memory(user_id: str, query: str):
    # Get user context from memory
    profile = m.profile(user_id=user_id)
    memories = m.search(query, user_id=user_id)

    # Build context-aware prompt
    context = "\\n".join([r.memory for r in memories])

    messages = [
        {{"role": "system", "content": profile}},
        {{"role": "user", "content": f"Relevant memories:\\n{{context}}\\n\\nQuery: {{query}}"}}
    ]

    response = llm.invoke(messages)

    # Store the interaction
    m.add(f"User: {{query}}\\nAgent: {{response.content}}", user_id=user_id)
    return response.content</code></pre>

<h2>What this enables</h2>
<ul>
<li><strong>Cross-session learning:</strong> Agents remember past research, decisions, and outcomes</li>
<li><strong>User-specific behavior:</strong> Each user gets personalized responses based on their history</li>
<li><strong>Workflow improvement:</strong> Procedural memory captures successful task patterns that evolve from failures</li>
<li><strong>Team memory:</strong> Multiple agents share a common memory space for collaborative knowledge</li>
</ul>

<h2>Multi-agent memory sharing</h2>
<pre><code># CrewAI agents sharing memory via the same user_id
researcher = Agent(role="Researcher", memory=True)
writer = Agent(role="Writer", memory=True)
reviewer = Agent(role="Reviewer", memory=True)

# All agents in the same crew share memory
# The researcher's findings are available to the writer
# The reviewer's feedback improves future workflows</code></pre>

<p>This is the power of <a href="/blog/semantic-episodic-procedural-memory">three memory types</a> — the researcher stores facts (semantic), the writer references past articles (episodic), and the reviewer's feedback updates the writing process (procedural).</p>

<p>Get started: <code>pip install mengram-ai</code> and grab a <a href="/#signup">free API key</a>. Full <a href="/blog/how-to-add-memory-to-ai-agents">quickstart tutorial here</a>.</p>
""",
            "related": ["how-to-add-memory-to-ai-agents", "mcp-memory-server-setup"],
        },
        "claude-code-memory-hooks": {
            "slug": "claude-code-memory-hooks",
            "title": "How to Add Persistent Memory to Claude Code (Auto-Save, Auto-Recall, Profile)",
            "date": "March 5, 2026",
            "date_iso": "2026-03-05",
            "read_time": "5",
            "tags": ["Tutorial", "Claude Code"],
            "excerpt": "Give Claude Code persistent memory with one command. Auto-save conversations, auto-recall context on every prompt, and load your cognitive profile on session start.",
            "seo_title": "How to Add Persistent Memory to Claude Code — Auto-Save & Auto-Recall Hooks | Mengram",
            "seo_description": "Step-by-step guide to adding persistent memory to Claude Code. Install auto-save, auto-recall, and cognitive profile hooks with one command. Free, open-source.",
            "seo_keywords": "Claude Code memory, Claude Code persistent memory, Claude Code hooks, Claude Code auto-save, Claude Code auto-recall, Claude Code cognitive profile, claude-mem alternative, Claude Code plugins, Claude Code remember, add memory to Claude Code",
            "content_html": """
<p>Claude Code is powerful, but it forgets everything when you start a new session. Your tech stack, your project structure, yesterday's debugging session — all gone. Let's fix that.</p>

<h2>The problem</h2>
<p>Every Claude Code session starts from zero. Claude doesn't know:</p>
<ul>
<li>Who you are or what you're working on</li>
<li>What you discussed yesterday</li>
<li>What bugs you fixed last week</li>
<li>Your preferred tools, frameworks, and patterns</li>
</ul>
<p>Some tools like claude-mem save conversations to files, but they never <em>recall</em> that information. Saving without retrieval is like a brain that records but never remembers.</p>

<h2>The solution: Full memory loop</h2>
<p>Mengram installs 3 Claude Code hooks that create a complete memory loop:</p>

<pre><code>pip install mengram-ai
mengram setup</code></pre>

<p>That's it — signup, key saving, and hook install all happen in the terminal. Here's what happens automatically:</p>

<pre><code>Session Start  →  Loads your cognitive profile
                  (who you are, preferences, tech stack)

Every Prompt   →  Searches past sessions for relevant context
                  (auto-recall via UserPromptSubmit hook)

After Response →  Saves new knowledge in background
                  (auto-save via Stop hook, async)</code></pre>

<h2>How it works under the hood</h2>

<h3>1. Session Context (SessionStart hook)</h3>
<p>When you start Claude Code, the <code>mengram auto-context</code> hook fires. It calls the Mengram API to load your <a href="/blog/cognitive-profile-system-prompts">Cognitive Profile</a> — a system prompt generated from everything Mengram knows about you. Claude sees this as context before your first message.</p>

<h3>2. Auto-Recall (UserPromptSubmit hook)</h3>
<p>On every prompt you type, <code>mengram auto-recall</code> searches your memory for relevant context. If you ask about "deployment issues," it finds facts about your deployment setup, past incidents, and relevant procedures. This context is injected via Claude Code's <code>additionalContext</code> mechanism — Claude sees it and uses it naturally.</p>

<h3>3. Auto-Save (Stop hook)</h3>
<p>After Claude responds, <code>mengram auto-save</code> runs in the background (async). It sends the conversation to Mengram's API, which extracts entities, facts, events, and workflows. By default it saves every 3rd response to avoid noise — configurable with <code>mengram hook install --every 5</code>.</p>

<h2>Managing hooks</h2>
<pre><code>mengram hook status      # Check what's installed
mengram hook uninstall   # Remove all hooks
mengram hook install --every 5  # Save every 5th response</code></pre>

<h2>Why not just use claude-mem?</h2>
<p>claude-mem saves conversations to local Markdown files. That's useful for logging, but:</p>
<ul>
<li><strong>No recall</strong> — it never searches past sessions or injects context</li>
<li><strong>No profile</strong> — Claude doesn't know who you are on session start</li>
<li><strong>No semantic search</strong> — you can't find relevant memories by meaning</li>
<li><strong>No structure</strong> — raw conversation dumps vs. extracted entities, facts, and workflows</li>
<li><strong>Local only</strong> — no sync across devices or tools</li>
</ul>
<p>See the <a href="/vs/claude-mem">full comparison</a>.</p>

<h2>Works beyond Claude Code</h2>
<p>The same memory is accessible via:</p>
<ul>
<li><a href="/blog/mcp-memory-server-setup">MCP Server</a> (29 tools) — Claude Desktop, Cursor, Windsurf</li>
<li><a href="/blog/ai-memory-for-crewai-langchain">LangChain & CrewAI</a> integrations</li>
<li>Python & JavaScript SDKs</li>
<li>REST API (90+ endpoints)</li>
</ul>
<p>Your memory follows you across every tool.</p>

<h2>Get started</h2>
<pre><code>pip install mengram-ai
mengram setup</code></pre>
<p>Restart Claude Code. That's it — Claude remembers now.</p>
""",
            "related": ["cognitive-profile-system-prompts", "mcp-memory-server-setup"],
        },
    }

    @app.get("/blog", response_class=HTMLResponse)
    async def blog_index():
        """Blog listing page."""
        template_path = Path(__file__).parent / "blog-index.html"
        html = template_path.read_text(encoding="utf-8")
        # Build posts HTML sorted by date (newest first)
        sorted_posts = sorted(BLOG_POSTS.values(), key=lambda p: p["date_iso"], reverse=True)
        posts_html = ""
        for p in sorted_posts:
            tags_html = "".join(f'<span class="tag">{t}</span>' for t in p.get("tags", []))
            posts_html += f'''<a href="/blog/{p["slug"]}" class="post-card">
                {tags_html}
                <h2>{p["title"]}</h2>
                <p>{p["excerpt"]}</p>
                <div class="post-meta"><span>{p["date"]}</span><span>{p["read_time"]} min read</span></div>
            </a>'''
        return html.replace("{posts_html}", posts_html)

    @app.get("/blog/{slug}", response_class=HTMLResponse)
    async def blog_post(slug: str):
        """Blog post page."""
        data = BLOG_POSTS.get(slug)
        if not data:
            raise HTTPException(404, "Blog post not found")
        template_path = Path(__file__).parent / "blog.html"
        html = template_path.read_text(encoding="utf-8")
        # Build related posts HTML
        related_html = ""
        for rs in data.get("related", []):
            rp = BLOG_POSTS.get(rs)
            if rp:
                related_html += f'<a href="/blog/{rp["slug"]}" class="related-card"><h3>{rp["title"]}</h3><p>{rp["excerpt"][:100]}...</p></a>'
        data_copy = {**data, "related_posts_html": related_html}
        return html.format(**data_copy)

    # ---- Use case pages (SEO) ----
    USECASE_PAGES = {
        "customer-support": {
            "slug": "customer-support",
            "industry": "customer support",
            "icon": "🎧",
            "title": "AI Memory for Customer Support Agents",
            "hero_description": "Support agents that remember every customer interaction. No more asking customers to repeat themselves.",
            "seo_title": "AI Memory for Customer Support Agents | Mengram",
            "seo_description": "Give your customer support AI agents persistent memory. Remember customer history, preferences, and past issues across every interaction. Reduce resolution time by 40%.",
            "seo_keywords": "AI memory customer support, AI customer service memory, support agent memory, customer context AI, persistent memory support bot",
            "pain_points": [
                ("Customers repeat themselves", "Every new session starts from zero. Customers explain their issue again and again across channels and agents."),
                ("No context between sessions", "When a customer returns, the AI has no idea about previous interactions, resolutions, or preferences."),
                ("Generic responses", "Without history, the AI gives cookie-cutter answers instead of personalized solutions based on the customer's product usage."),
                ("Slow resolution times", "Agents spend time gathering context instead of solving problems. Each ticket starts from scratch."),
            ],
            "solutions": [
                ("Full customer history", "Semantic memory stores customer preferences, plan details, and product usage. Episodic memory recalls past issues and resolutions."),
                ("Cross-session continuity", "Every interaction enriches the customer's memory. Next time they reach out, the AI already knows their history."),
                ("Personalized resolution", "Cognitive Profile generates a system prompt with everything known about the customer — preferences, history, and escalation patterns."),
                ("Workflow learning", "Procedural memory captures resolution workflows that improve from failures. The AI learns the best process for each issue type."),
            ],
            "code_example": """from mengram import Mengram
from openai import OpenAI

m = Mengram(api_key="mg-...")
openai = OpenAI()

def handle_ticket(customer_id: str, message: str):
    # Get full customer context in one call
    profile = m.profile(user_id=customer_id)
    past_issues = m.search(message, user_id=customer_id, top_k=3)

    context = "\\n".join([r.memory for r in past_issues])

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": profile},
            {"role": "user", "content": f"Past issues:\\n{context}\\n\\nNew message: {message}"}
        ]
    )

    # Store this interaction for future context
    m.add(f"Customer: {message}\\nAgent: {response.choices[0].message.content}",
          user_id=customer_id)
    return response.choices[0].message.content""",
            "benefits": [
                ("40%", "Faster resolution"),
                ("3x", "Customer satisfaction"),
                ("Zero", "Context switching"),
            ],
        },
        "personal-assistant": {
            "slug": "personal-assistant",
            "industry": "personal assistant",
            "icon": "🤖",
            "title": "AI Memory for Personal Assistants",
            "hero_description": "Build AI assistants that truly know their users. Remember preferences, habits, and context across every conversation.",
            "seo_title": "AI Memory for Personal Assistants | Mengram",
            "seo_description": "Build AI personal assistants with persistent memory. Remember user preferences, habits, schedules, and context. Cognitive Profile for instant personalization.",
            "seo_keywords": "AI personal assistant memory, persistent memory assistant, AI companion memory, personalized AI assistant, Mengram personal assistant",
            "pain_points": [
                ("Every day is day one", "Personal assistants forget everything between sessions. Users re-explain preferences, projects, and context every time."),
                ("No personalization", "Without memory, the assistant gives generic responses that don't reflect the user's unique needs and style."),
                ("Can't learn habits", "The assistant can't recognize patterns in the user's behavior — daily routines, recurring tasks, or preferred workflows."),
                ("No relationship building", "AI companions feel shallow because they don't accumulate shared experiences or inside references."),
            ],
            "solutions": [
                ("Deep personalization", "Semantic memory stores preferences, interests, and personal details. The AI knows the user inside and out."),
                ("Shared history", "Episodic memory remembers conversations, decisions, and events. The AI references past interactions naturally."),
                ("Learned routines", "Procedural memory captures daily workflows, recurring tasks, and preferred processes that evolve over time."),
                ("Cognitive Profile", "One API call generates a system prompt with the user's full context — making every LLM instantly personalized."),
            ],
            "code_example": """from mengram import Mengram

m = Mengram(api_key="mg-...")

# Morning check-in — AI remembers everything
profile = m.profile(user_id="alice")
# "Alice is a product manager at Acme Corp. She prefers morning standup
#  summaries with bullet points. She's working on the Q1 launch...
#  Yesterday she reviewed the design specs and had feedback on the nav..."

# After each conversation, memory grows
m.add("Alice asked me to remind her about the design review on Friday. "
      "She also mentioned she prefers Figma links over screenshots.",
      user_id="alice")

# Next session: the AI remembers the reminder and preference
memories = m.search("design review", user_id="alice")""",
            "benefits": [
                ("100%", "Context retention"),
                ("∞", "Session continuity"),
                ("3 types", "Memory depth"),
            ],
        },
        "education": {
            "slug": "education",
            "industry": "education",
            "icon": "📚",
            "title": "AI Memory for Education & Adaptive Tutoring",
            "hero_description": "AI tutors that remember what each student knows, where they struggle, and how they learn best.",
            "seo_title": "AI Memory for Education & Adaptive Tutoring | Mengram",
            "seo_description": "Build AI tutors with persistent memory. Track student knowledge, learning style, and progress. Adaptive tutoring that gets smarter with every session.",
            "seo_keywords": "AI memory education, AI tutoring memory, adaptive learning AI, personalized education AI, AI tutor memory, Mengram education",
            "pain_points": [
                ("No student model", "AI tutors don't track what the student knows vs. doesn't know. They can't adapt difficulty or skip mastered topics."),
                ("Repeated explanations", "Students get the same explanation style even when it didn't work before. No adaptation to individual learning patterns."),
                ("Lost progress", "Each tutoring session starts fresh. Past mistakes, breakthroughs, and learning trajectory are forgotten."),
                ("One-size-fits-all", "Without memory, every student gets the same experience regardless of their level, goals, or learning speed."),
            ],
            "solutions": [
                ("Knowledge tracking", "Semantic memory stores what each student knows, their knowledge gaps, and mastery levels per topic."),
                ("Learning history", "Episodic memory records tutoring sessions — which explanations worked, what confused the student, key breakthroughs."),
                ("Teaching strategies", "Procedural memory captures effective tutoring approaches per student that improve over time."),
                ("Adaptive profiles", "Cognitive Profile generates a tutor system prompt with the student's full context — level, preferences, and history."),
            ],
            "code_example": """from mengram import Mengram

m = Mengram(api_key="mg-...")

def tutor_session(student_id: str, topic: str):
    # Get student's full learning profile
    profile = m.profile(user_id=student_id)
    # "Student is a 10th grader studying calculus. Strong in algebra,
    #  struggles with limits. Learns best with visual examples.
    #  Last session: practiced chain rule, got 7/10 correct."

    past = m.search(topic, user_id=student_id)
    # Returns past interactions with this topic

    # After the session, store progress
    m.add(f"Tutored {topic}. Student understood the concept after "
          f"visual explanation with graphs. Scored 8/10 on practice.",
          user_id=student_id)""",
            "benefits": [
                ("2x", "Learning speed"),
                ("85%", "Retention rate"),
                ("Per-student", "Adaptation"),
            ],
        },
        "healthcare": {
            "slug": "healthcare",
            "industry": "healthcare",
            "icon": "🏥",
            "title": "AI Memory for Healthcare Agents",
            "hero_description": "Healthcare AI that remembers patient context, medical history, and care preferences across every interaction.",
            "seo_title": "AI Memory for Healthcare Agents | Mengram",
            "seo_description": "Build healthcare AI agents with persistent memory. Track patient context, medical preferences, and care history. Self-hostable for data sovereignty.",
            "seo_keywords": "AI memory healthcare, healthcare AI memory, patient context AI, medical AI memory, healthcare agent memory, Mengram healthcare",
            "pain_points": [
                ("Repeated intake questions", "Patients describe their history, medications, and symptoms every time they interact with the AI assistant."),
                ("No care continuity", "AI health assistants don't track conversations over time — missing patterns in symptoms, mood, or behavior."),
                ("Generic health advice", "Without patient context, AI gives generic recommendations instead of personalized guidance based on history."),
                ("Data sovereignty concerns", "Healthcare data must stay within controlled environments. Cloud-only solutions don't meet compliance needs."),
            ],
            "solutions": [
                ("Patient context", "Semantic memory stores patient preferences, conditions, and care notes. Always available for personalized interactions."),
                ("Interaction history", "Episodic memory tracks symptom reports, mood changes, and care interactions over time — surfacing patterns."),
                ("Care workflows", "Procedural memory captures proven care pathways and follow-up procedures that improve with each patient interaction."),
                ("Self-hostable", "Deploy Mengram on your own infrastructure. All memory stays within your data boundary. MIT licensed."),
            ],
            "code_example": """from mengram import Mengram

# Self-hosted for data sovereignty
m = Mengram(base_url="https://your-mengram.internal.com")

def patient_interaction(patient_id: str, message: str):
    # Full patient context in one call
    profile = m.profile(user_id=patient_id)
    # "Patient is managing Type 2 diabetes. Prefers morning check-ins.
    #  Last reported A1C: 7.2%. Current medications: metformin.
    #  Last visit: discussed increasing exercise routine."

    # Search for relevant history
    history = m.search(message, user_id=patient_id)

    # After interaction, store for continuity
    m.add(f"Patient reported: {message}", user_id=patient_id)""",
            "benefits": [
                ("100%", "Context retention"),
                ("Self-host", "Data sovereignty"),
                ("HIPAA", "Ready architecture"),
            ],
        },
        "sales": {
            "slug": "sales",
            "industry": "sales",
            "icon": "💼",
            "title": "AI Memory for Sales & SDR Agents",
            "hero_description": "Sales AI that remembers every prospect interaction, objection, and follow-up across the entire pipeline.",
            "seo_title": "AI Memory for Sales & SDR Agents | Mengram",
            "seo_description": "Build sales AI agents with persistent memory. Track prospect interactions, objections, pain points, and follow-ups. AI SDR that gets smarter with every call.",
            "seo_keywords": "AI memory sales, AI SDR memory, sales agent memory, prospect context AI, AI sales assistant, Mengram sales",
            "pain_points": [
                ("Cold outreach feels cold", "AI SDRs send generic messages because they don't remember past interactions or prospect context."),
                ("Lost follow-up context", "Between calls, the AI forgets what was discussed — objections raised, interests expressed, next steps agreed."),
                ("No objection learning", "Every objection is handled from scratch. The AI doesn't learn which responses work best for each prospect type."),
                ("Pipeline blind spots", "Without memory, AI can't track where each prospect is in the journey or what triggered their interest."),
            ],
            "solutions": [
                ("Prospect intelligence", "Semantic memory stores company info, role, pain points, and interests discovered across interactions."),
                ("Full interaction history", "Episodic memory records every call, email, and meeting — what was discussed, what resonated, what fell flat."),
                ("Objection playbooks", "Procedural memory captures winning responses to common objections that improve from successful closes."),
                ("Pipeline context", "Cognitive Profile generates a briefing for each prospect — full history, next steps, and recommended approach."),
            ],
            "code_example": """from mengram import Mengram

m = Mengram(api_key="mg-...")

def prep_for_call(prospect_id: str):
    # Get full prospect briefing
    profile = m.profile(user_id=prospect_id)
    # "Prospect is VP Engineering at TechCo (Series B, 50 engineers).
    #  Pain point: context switching between tools.
    #  Last call: interested in the API, asked about pricing.
    #  Objection: concerned about vendor lock-in.
    #  Next step: send case study from similar company."

    return profile

def after_call(prospect_id: str, notes: str):
    # Store call outcome for next interaction
    m.add(notes, user_id=prospect_id)
    # "Called prospect. Addressed vendor lock-in concern with MIT license
    #  and self-hosting option. They want a demo next Tuesday."
""",
            "benefits": [
                ("3x", "Response rate"),
                ("60%", "Faster pipeline"),
                ("Zero", "Context loss"),
            ],
        },
    }

    @app.get("/usecase/{slug}", response_class=HTMLResponse)
    async def usecase_page(slug: str):
        """Use case page for specific industry."""
        data = USECASE_PAGES.get(slug)
        if not data:
            raise HTTPException(404, "Use case page not found")
        template_path = Path(__file__).parent / "usecase.html"
        html = template_path.read_text(encoding="utf-8")
        # Build pain points HTML
        pain_html = ""
        for title, desc in data["pain_points"]:
            pain_html += f'<div class="pain-card problem"><h3>{title}</h3><p>{desc}</p></div>'
        # Build solutions HTML
        sol_html = ""
        for title, desc in data["solutions"]:
            sol_html += f'<div class="pain-card solution"><h3>{title}</h3><p>{desc}</p></div>'
        # Build benefits HTML
        ben_html = ""
        for num, label in data["benefits"]:
            ben_html += f'<div class="benefit"><div class="num">{num}</div><p>{label}</p></div>'
        data_copy = {
            **data,
            "pain_points_html": pain_html,
            "solution_html": sol_html,
            "benefits_html": ben_html,
        }
        return html.format(**data_copy)

    # ---- Documentation Pages ----

    DOCS_SIDEBAR = [
        ("Getting Started", [
            ("quickstart", "Quickstart"),
            ("memory-types", "Memory Types"),
            ("cognitive-profile", "Cognitive Profile"),
        ]),
        ("SDKs", [
            ("python-sdk", "Python SDK"),
            ("async-client", "Async Client"),
            ("javascript-sdk", "JavaScript SDK"),
        ]),
        ("Integrations", [
            ("langchain", "LangChain"),
            ("crewai", "CrewAI"),
            ("mcp", "MCP Server"),
            ("n8n", "n8n"),
        ]),
        ("Reference", [
            ("api-reference", "API Reference"),
            ("search-filters", "Search & Filters"),
            ("webhooks", "Webhooks"),
        ]),
    ]

    def _build_sidebar(active_slug: str) -> str:
        html = ""
        for section, pages in DOCS_SIDEBAR:
            html += f'<div class="sidebar-section"><h4>{section}</h4>'
            for slug, title in pages:
                cls = ' class="active"' if slug == active_slug else ""
                html += f'<a href="/docs/{slug}"{cls}>{title}</a>'
            html += "</div>"
        return html

    DOCS_PAGES = {
        "quickstart": {
            "title": "Quickstart",
            "description": "Get your API key and add your first memory in under 2 minutes.",
            "content": """
<h2>1. Get an API key</h2>
<p>Sign up at <a href="/#signup">mengram.io</a> to get your free API key. It starts with <code>om-</code>.</p>

<h2>2. Install the SDK</h2>
<h3>Python</h3>
<pre><code>pip install mengram-ai</code></pre>
<h3>JavaScript</h3>
<pre><code>npm install mengram-ai</code></pre>

<h2>3. Add your first memory</h2>
<h3>Python</h3>
<pre><code>from mengram import Mengram

m = Mengram(api_key="om-your-key")

# Add memories from a conversation
result = m.add([
    {{"role": "user", "content": "I deployed the app on Railway. Using PostgreSQL."}},
    {{"role": "assistant", "content": "Got it, noted the Railway + PostgreSQL stack."}},
])

# result contains a job_id for background processing
print(result)  # {{"status": "accepted", "job_id": "job-..."}}</code></pre>

<h3>JavaScript</h3>
<pre><code>const {{ MengramClient }} = require('mengram-ai');
const m = new MengramClient('om-your-key');

await m.add([
    {{ role: 'user', content: 'I deployed the app on Railway. Using PostgreSQL.' }},
]);</code></pre>

<h2>4. Search your memories</h2>
<pre><code># Semantic search
results = m.search("deployment stack")
for r in results:
    print(f"{{r['entity']}} (score={{r['score']:.2f}})")
    for fact in r.get("facts", []):
        print(f"  - {{fact}}")

# Unified search — all 3 memory types at once
all_results = m.search_all("deployment issues")
print(all_results["semantic"])    # knowledge graph results
print(all_results["episodic"])    # events and experiences
print(all_results["procedural"]) # learned workflows</code></pre>

<h2>5. Get a Cognitive Profile</h2>
<p>Generate a ready-to-use system prompt that captures who a user is:</p>
<pre><code>profile = m.get_profile()
system_prompt = profile["system_prompt"]

# Use in any LLM call
response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[
        {{"role": "system", "content": system_prompt}},
        {{"role": "user", "content": "What should I work on next?"}},
    ]
)</code></pre>

<div class="tip"><strong>Tip:</strong> Use the environment variable <code>MENGRAM_API_KEY</code> so you don't have to pass the key every time: <code>m = Mengram()</code></div>
""",
        },
        "memory-types": {
            "title": "Memory Types",
            "description": "Understand semantic, episodic, and procedural memory — the three pillars of human-like AI memory.",
            "content": """
<h2>Overview</h2>
<p>Mengram gives your AI three distinct memory types, inspired by how human memory works:</p>
<table>
<tr><th>Type</th><th>Stores</th><th>Example</th></tr>
<tr><td><strong>Semantic</strong></td><td>Facts, knowledge, preferences</td><td>"User prefers dark mode and uses Python 3.12"</td></tr>
<tr><td><strong>Episodic</strong></td><td>Events, experiences, interactions</td><td>"Fixed an OOM bug on Jan 15 by reducing pool size"</td></tr>
<tr><td><strong>Procedural</strong></td><td>Workflows, processes, skills</td><td>"How to deploy: 1) run tests, 2) build, 3) push to main"</td></tr>
</table>
<p>When you call <code>m.add(messages)</code>, all three types are extracted automatically from the conversation.</p>

<h2>Semantic Memory</h2>
<p>The knowledge graph. Entities with facts, types, and relationships. This is the core memory layer.</p>
<pre><code># Search semantic memory
results = m.search("user preferences")
# Returns entities with facts and scores

# Get a specific entity
entity = m.get("PostgreSQL")
# {{"name": "PostgreSQL", "type": "technology", "facts": [...]}}</code></pre>

<h2>Episodic Memory</h2>
<p>Autobiographical events — what happened, when, with whom, and what the outcome was. Each episode has a summary, context, outcome, and participant list.</p>
<pre><code># Search episodes
events = m.episodes(query="deployment issues")
# [{{"summary": "Fixed OOM on Railway", "outcome": "Resolved by reducing pool", ...}}]

# List recent episodes
recent = m.episodes(limit=10)

# Time-range filter
jan_events = m.episodes(after="2026-01-01", before="2026-02-01")</code></pre>

<h2>Procedural Memory</h2>
<p>Learned workflows and processes. Mengram extracts step-by-step procedures from conversations and tracks which ones work and which fail.</p>
<pre><code># Search procedures
procs = m.procedures(query="deploy")
# [{{"name": "Deploy to Railway", "steps": [...], "success_count": 5}}]

# Report success/failure — triggers experience-driven evolution
m.procedure_feedback(proc_id, success=True)

# On failure with context, the procedure evolves automatically
m.procedure_feedback(proc_id, success=False,
    context="Step 3 failed: OOM on build",
    failed_at_step=3)

# View how a procedure evolved over time
history = m.procedure_history(proc_id)
# {{"versions": [v1, v2, v3], "evolution_log": [...]}}</code></pre>

<h2>Unified Search</h2>
<p>Search all three types at once with a single call:</p>
<pre><code>results = m.search_all("deployment problems")
# {{
#     "semantic": [...],    # knowledge graph entities
#     "episodic": [...],    # related events
#     "procedural": [...]   # relevant workflows
# }}</code></pre>
""",
        },
        "cognitive-profile": {
            "title": "Cognitive Profile",
            "description": "Generate a ready-to-use system prompt from memory that captures who a user is, their preferences, and current focus.",
            "content": """
<h2>What is a Cognitive Profile?</h2>
<p>A Cognitive Profile is an AI-generated system prompt that summarizes everything Mengram knows about a user: identity, preferences, communication style, current projects, and key relationships. Insert it into any LLM's system prompt for instant personalization.</p>

<h2>Generate a profile</h2>
<pre><code>from mengram import Mengram

m = Mengram()
profile = m.get_profile()

print(profile["system_prompt"])
# "You are talking to Ali, a software engineer based in ...
#  He prefers concise responses, uses Python and Railway..."

print(profile["facts_used"])  # 47 — number of facts used</code></pre>

<h2>Use in an LLM call</h2>
<pre><code>import openai

profile = m.get_profile(user_id="alice")

response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[
        {{"role": "system", "content": profile["system_prompt"]}},
        {{"role": "user", "content": "What should I focus on this week?"}},
    ]
)</code></pre>

<h2>Force regeneration</h2>
<p>Profiles are cached for performance. Force a fresh one with:</p>
<pre><code>profile = m.get_profile(force=True)</code></pre>

<h2>Multi-user profiles</h2>
<p>Generate profiles for different end-users in your app:</p>
<pre><code>alice_profile = m.get_profile(user_id="alice")
bob_profile = m.get_profile(user_id="bob")</code></pre>

<h2>LangChain integration</h2>
<pre><code>from langchain_mengram import get_mengram_profile

# Returns a string you can use as system prompt
prompt = get_mengram_profile(api_key="om-...", user_id="alice")</code></pre>
""",
        },
        "python-sdk": {
            "title": "Python SDK",
            "description": "Full reference for the Mengram Python client — zero external dependencies, works everywhere.",
            "content": """
<h2>Installation</h2>
<pre><code>pip install mengram-ai</code></pre>

<h2>Initialize</h2>
<pre><code>from mengram import Mengram

# Pass API key directly
m = Mengram(api_key="om-your-key")

# Or use environment variable
# export MENGRAM_API_KEY=om-your-key
m = Mengram()</code></pre>

<h2>Core methods</h2>

<h3>add(messages, ...)</h3>
<p>Add memories from a conversation. Automatically extracts entities, facts, episodes, and procedures.</p>
<pre><code>result = m.add([
    {{"role": "user", "content": "We fixed the OOM with Redis cache"}},
    {{"role": "assistant", "content": "Noted the Redis cache fix."}},
])
# Returns: {{"status": "accepted", "job_id": "job-..."}}</code></pre>
<table>
<tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr>
<tr><td><code>messages</code></td><td>list[dict]</td><td>required</td><td>Chat messages with role and content</td></tr>
<tr><td><code>user_id</code></td><td>str</td><td>"default"</td><td>User identifier for multi-user isolation</td></tr>
<tr><td><code>agent_id</code></td><td>str</td><td>None</td><td>Agent identifier</td></tr>
<tr><td><code>run_id</code></td><td>str</td><td>None</td><td>Session/run identifier</td></tr>
<tr><td><code>app_id</code></td><td>str</td><td>None</td><td>Application identifier</td></tr>
<tr><td><code>expiration_date</code></td><td>str</td><td>None</td><td>ISO datetime — facts auto-expire</td></tr>
</table>

<h3>add_text(text, ...)</h3>
<p>Add memories from plain text instead of chat messages.</p>
<pre><code>m.add_text("Meeting notes: decided to migrate to PostgreSQL 16")</code></pre>

<h3>search(query, ...)</h3>
<p>Semantic search across the knowledge graph.</p>
<pre><code>results = m.search("database preferences", limit=10)
for r in results:
    print(f"{{r['entity']}} — score: {{r['score']:.2f}}")
    for fact in r.get("facts", []):
        print(f"  • {{fact}}")</code></pre>
<table>
<tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr>
<tr><td><code>query</code></td><td>str</td><td>required</td><td>Natural language search query</td></tr>
<tr><td><code>limit</code></td><td>int</td><td>5</td><td>Max results</td></tr>
<tr><td><code>graph_depth</code></td><td>int</td><td>2</td><td>Knowledge graph traversal depth</td></tr>
<tr><td><code>filters</code></td><td>dict</td><td>None</td><td>Metadata filters</td></tr>
</table>

<h3>search_all(query, ...)</h3>
<p>Unified search across all 3 memory types.</p>
<pre><code>results = m.search_all("deployment")
print(results["semantic"])     # entities
print(results["episodic"])     # events
print(results["procedural"])   # workflows</code></pre>

<h3>get_all() / get(name) / delete(name)</h3>
<pre><code>memories = m.get_all()           # list all entities
entity = m.get("PostgreSQL")     # get specific entity
m.delete("PostgreSQL")           # delete entity</code></pre>

<h3>get_profile(...)</h3>
<p>Generate a Cognitive Profile. See <a href="https://docs.mengram.io/cognitive-profile">Cognitive Profile docs</a>.</p>

<h3>episodes(...)</h3>
<p>Search or list episodic memories.</p>
<pre><code>events = m.episodes(query="auth bug", limit=5)
recent = m.episodes(limit=20)
jan = m.episodes(after="2026-01-01", before="2026-02-01")</code></pre>

<h3>procedures(...)</h3>
<p>Search or list procedural memories.</p>
<pre><code>procs = m.procedures(query="deploy")
all_procs = m.procedures(limit=50)</code></pre>

<h3>procedure_feedback(id, ...)</h3>
<p>Report success/failure. Triggers experience-driven evolution on failure with context.</p>
<pre><code>m.procedure_feedback(proc_id, success=True)
m.procedure_feedback(proc_id, success=False,
    context="Build OOM", failed_at_step=3)</code></pre>

<h2>Memory management</h2>
<pre><code>m.dedup()                    # find and merge duplicates
m.merge("src", "target")    # merge two entities
m.archive_fact("Entity", "old fact")  # archive a fact
m.run_agents()               # run curator, connector, digest agents
m.stats()                    # usage statistics</code></pre>

<h2>Webhooks</h2>
<pre><code>m.create_webhook(url="https://example.com/hook",
    event_types=["memory_add", "memory_update"])
hooks = m.get_webhooks()</code></pre>

<h2>Import data</h2>
<pre><code># Import ChatGPT export
m.import_chatgpt("~/Downloads/chatgpt-export.zip")

# Import Obsidian vault
m.import_obsidian("~/Documents/MyVault")

# Import text/markdown files
m.import_files(["notes.md", "journal.txt"])</code></pre>
""",
        },
        "async-client": {
            "title": "Async Client",
            "description": "Non-blocking Python client built on httpx for async/await workflows.",
            "content": """
<h2>Installation</h2>
<pre><code>pip install mengram-ai[async]</code></pre>
<p>This installs <code>httpx</code> for non-blocking HTTP.</p>

<h2>Initialize</h2>
<pre><code>from mengram import AsyncMengram

m = AsyncMengram(api_key="om-your-key")

# Or use environment variable
m = AsyncMengram()</code></pre>

<h2>Context manager</h2>
<pre><code>async with AsyncMengram() as m:
    results = await m.search("deployment")
    profile = await m.get_profile()
# Client automatically closed</code></pre>

<h2>All methods are async</h2>
<p>Every method from the sync client has an async equivalent:</p>
<pre><code>import asyncio
from mengram import AsyncMengram

async def main():
    m = AsyncMengram()

    # Add memories
    result = await m.add([
        {{"role": "user", "content": "Deployed on Railway with PostgreSQL"}},
    ])

    # Search
    results = await m.search("deployment")

    # Unified search
    all_results = await m.search_all("issues")

    # Profile
    profile = await m.get_profile()

    # Episodes & procedures
    events = await m.episodes(query="deployment")
    procs = await m.procedures(query="deploy")

    # Close when done
    await m.close()

asyncio.run(main())</code></pre>

<h2>API parity</h2>
<p>The async client has the same methods as the sync client. Just add <code>await</code> before each call.</p>
<table>
<tr><th>Sync</th><th>Async</th></tr>
<tr><td><code>m.add(msgs)</code></td><td><code>await m.add(msgs)</code></td></tr>
<tr><td><code>m.search(q)</code></td><td><code>await m.search(q)</code></td></tr>
<tr><td><code>m.search_all(q)</code></td><td><code>await m.search_all(q)</code></td></tr>
<tr><td><code>m.get_profile()</code></td><td><code>await m.get_profile()</code></td></tr>
<tr><td><code>m.episodes()</code></td><td><code>await m.episodes()</code></td></tr>
</table>

<h2>Retry &amp; error handling</h2>
<p>The async client automatically retries on transient errors (429, 502, 503, 504) and network failures, with exponential backoff up to 3 attempts.</p>
""",
        },
        "javascript-sdk": {
            "title": "JavaScript SDK",
            "description": "Node.js and browser SDK for Mengram with full TypeScript support.",
            "content": """
<h2>Installation</h2>
<pre><code>npm install mengram-ai</code></pre>

<h2>Quick start</h2>
<pre><code>const {{ MengramClient }} = require('mengram-ai');
const m = new MengramClient('om-your-api-key');

// Add memories
await m.add([
    {{ role: 'user', content: 'Fixed the auth bug using rate limiting.' }},
]);

// Semantic search
const results = await m.search('auth issues');

// Unified search — all 3 types
const all = await m.searchAll('deployment issues');
// {{ semantic: [...], episodic: [...], procedural: [...] }}

// Cognitive Profile
const profile = await m.getProfile('alice');
// {{ system_prompt: "You are talking to Alice..." }}</code></pre>

<h2>TypeScript</h2>
<pre><code>import {{ MengramClient, SearchResult, Episode, Procedure }} from 'mengram-ai';

const m = new MengramClient('om-...');

const results: SearchResult[] = await m.search('preferences');
const events: Episode[] = await m.episodes({{ query: 'deployment' }});
const procs: Procedure[] = await m.procedures({{ query: 'release' }});</code></pre>

<h2>All methods</h2>
<table>
<tr><th>Method</th><th>Description</th></tr>
<tr><td><code>add(messages, options?)</code></td><td>Add memories (extracts all 3 types)</td></tr>
<tr><td><code>addText(text, options?)</code></td><td>Add from plain text</td></tr>
<tr><td><code>search(query, options?)</code></td><td>Semantic search</td></tr>
<tr><td><code>searchAll(query, options?)</code></td><td>Unified search (all 3 types)</td></tr>
<tr><td><code>episodes(options?)</code></td><td>Search/list episodic memories</td></tr>
<tr><td><code>procedures(options?)</code></td><td>Search/list procedural memories</td></tr>
<tr><td><code>procedureFeedback(id, opts)</code></td><td>Record success/failure</td></tr>
<tr><td><code>procedureHistory(id)</code></td><td>Version history</td></tr>
<tr><td><code>getProfile(userId?, opts?)</code></td><td>Cognitive Profile</td></tr>
<tr><td><code>getAll(options?)</code></td><td>List all memories</td></tr>
<tr><td><code>get(name)</code></td><td>Get specific entity</td></tr>
<tr><td><code>delete(name)</code></td><td>Delete entity</td></tr>
<tr><td><code>runAgents(options?)</code></td><td>Run memory agents</td></tr>
</table>

<h2>Multi-user isolation</h2>
<pre><code>// Each userId gets its own memory space
await m.add([{{ role: 'user', content: 'I prefer dark mode' }}], {{ userId: 'alice' }});
await m.add([{{ role: 'user', content: 'I prefer light mode' }}], {{ userId: 'bob' }});

const alice = await m.searchAll('preferences', {{ userId: 'alice' }});
// Only Alice's memories</code></pre>

<h2>Import data</h2>
<pre><code>// ChatGPT export (requires jszip)
await m.importChatgpt('~/Downloads/chatgpt-export.zip');

// Obsidian vault
await m.importObsidian('~/Documents/MyVault');

// Text/markdown files
await m.importFiles(['notes.md', 'journal.txt']);</code></pre>
""",
        },
        "langchain": {
            "title": "LangChain",
            "description": "Use MengramRetriever in LangChain RAG pipelines and chains for persistent memory.",
            "content": """
<h2>Installation</h2>
<pre><code>pip install langchain-mengram</code></pre>

<h2>MengramRetriever</h2>
<p>Subclasses <code>BaseRetriever</code> from LangChain. Searches across all 3 memory types and returns <code>Document</code> objects.</p>
<pre><code>from langchain_mengram import MengramRetriever

retriever = MengramRetriever(
    api_key="om-your-key",
    user_id="alice",
    top_k=5,
    memory_types=["semantic", "episodic", "procedural"],
)

# Use as any LangChain retriever
docs = retriever.invoke("deployment issues")
for doc in docs:
    print(doc.page_content)
    print(doc.metadata)  # {{"source": "mengram", "memory_type": "semantic", ...}}</code></pre>

<h2>Use in a chain</h2>
<pre><code>from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

retriever = MengramRetriever(api_key="om-...")

prompt = ChatPromptTemplate.from_template(
    "Context from memory:\\n{{context}}\\n\\nQuestion: {{question}}"
)

chain = (
    {{"context": retriever, "question": RunnablePassthrough()}}
    | prompt
    | ChatOpenAI(model="gpt-4o")
    | StrOutputParser()
)

answer = chain.invoke("What deployment stack am I using?")</code></pre>

<h2>Cognitive Profile</h2>
<pre><code>from langchain_mengram import get_mengram_profile

# Get a system prompt string
prompt = get_mengram_profile(api_key="om-...", user_id="alice")</code></pre>

<h2>Parameters</h2>
<table>
<tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr>
<tr><td><code>api_key</code></td><td>str</td><td>required</td><td>Mengram API key</td></tr>
<tr><td><code>user_id</code></td><td>str</td><td>"default"</td><td>User to search</td></tr>
<tr><td><code>api_url</code></td><td>str</td><td>"https://mengram.io"</td><td>API base URL</td></tr>
<tr><td><code>top_k</code></td><td>int</td><td>5</td><td>Max results per type</td></tr>
<tr><td><code>memory_types</code></td><td>list</td><td>all 3</td><td>Which types to search</td></tr>
</table>
""",
        },
        "crewai": {
            "title": "CrewAI",
            "description": "Give your CrewAI agents persistent memory with procedural learning.",
            "content": """
<h2>Installation</h2>
<pre><code>pip install mengram-ai[crewai]</code></pre>

<h2>Quick start</h2>
<pre><code>from integrations.crewai import create_mengram_tools  # included in mengram-ai
from crewai import Agent, Task, Crew

# Create memory tools
tools = create_mengram_tools(api_key="om-your-key")

agent = Agent(
    role="Support Engineer",
    goal="Help users with technical issues using past context",
    tools=tools,
)

task = Task(
    description="Help the user debug their deployment issue",
    agent=agent,
)

crew = Crew(agents=[agent], tasks=[task])
result = crew.kickoff()</code></pre>

<h2>Available tools</h2>
<table>
<tr><th>Tool</th><th>Description</th></tr>
<tr><td><code>mengram_search</code></td><td>Search all 3 memory types (semantic, episodic, procedural)</td></tr>
<tr><td><code>mengram_remember</code></td><td>Save information to memory (auto-extracts all 3 types)</td></tr>
<tr><td><code>mengram_profile</code></td><td>Get full user context via Cognitive Profile</td></tr>
<tr><td><code>mengram_save_workflow</code></td><td>Save a completed workflow as a procedure</td></tr>
<tr><td><code>mengram_workflow_feedback</code></td><td>Report success/failure of a workflow</td></tr>
</table>

<h2>Procedural learning</h2>
<p>When a CrewAI agent completes a multi-step task, Mengram automatically saves it as a procedure. Next time a similar task comes up, the agent already knows the optimal path — with success/failure tracking.</p>
<pre><code># Agent workflow is automatically extracted as a procedure
# On next similar task, the agent retrieves the procedure
results = tools[0].run("how to deploy to Railway")
# Returns the learned procedure with steps</code></pre>
""",
        },
        "mcp": {
            "title": "MCP Server",
            "description": "Use Mengram as a Model Context Protocol server with Claude Desktop, Cursor, and other MCP clients.",
            "content": """
<h2>What is MCP?</h2>
<p>Model Context Protocol (MCP) lets AI clients like Claude Desktop and Cursor connect to external tools. Mengram's MCP server gives these clients persistent memory.</p>

<h2>Setup with Claude Desktop</h2>
<p>Add to your Claude Desktop config (<code>claude_desktop_config.json</code>):</p>
<pre><code>{{
  "mcpServers": {{
    "mengram": {{
      "command": "uvx",
      "args": ["mengram-ai"],
      "env": {{
        "MENGRAM_API_KEY": "om-your-key"
      }}
    }}
  }}
}}</code></pre>

<h2>Setup with Cursor</h2>
<p>Add to Cursor's MCP settings:</p>
<pre><code>{{
  "mcpServers": {{
    "mengram": {{
      "command": "uvx",
      "args": ["mengram-ai"],
      "env": {{
        "MENGRAM_API_KEY": "om-your-key"
      }}
    }}
  }}
}}</code></pre>

<h2>Available tools</h2>
<p>The MCP server exposes 29 tools:</p>
<table>
<tr><th>Tool</th><th>Description</th></tr>
<tr><td><code>remember</code></td><td>Save knowledge from conversation to memory</td></tr>
<tr><td><code>remember_text</code></td><td>Save knowledge from plain text</td></tr>
<tr><td><code>recall</code></td><td>Semantic search through memory</td></tr>
<tr><td><code>search</code></td><td>Structured search with scores and facts</td></tr>
<tr><td><code>search_all</code></td><td>Unified search across all 3 memory types</td></tr>
<tr><td><code>timeline</code></td><td>Search memory by time range</td></tr>
<tr><td><code>vault_stats</code></td><td>Memory statistics</td></tr>
<tr><td><code>run_agents</code></td><td>Run memory agents (curator, connector, digest)</td></tr>
<tr><td><code>get_insights</code></td><td>AI-generated insights and patterns</td></tr>
<tr><td><code>list_procedures</code></td><td>List learned workflows/procedures</td></tr>
<tr><td><code>procedure_feedback</code></td><td>Record success/failure for a procedure</td></tr>
<tr><td><code>procedure_history</code></td><td>Version history of a procedure</td></tr>
<tr><td><code>get_entity</code></td><td>Get details of a specific entity</td></tr>
<tr><td><code>delete_entity</code></td><td>Delete an entity and all its data</td></tr>
<tr><td><code>list_episodes</code></td><td>List or search episodic memories</td></tr>
<tr><td><code>get_graph</code></td><td>Get the knowledge graph</td></tr>
<tr><td><code>get_triggers</code></td><td>List smart triggers and reminders</td></tr>
<tr><td><code>get_feed</code></td><td>Activity feed — recent memory changes</td></tr>
<tr><td><code>archive_fact</code></td><td>Archive a specific fact on an entity</td></tr>
<tr><td><code>merge_entities</code></td><td>Merge two entities into one</td></tr>
<tr><td><code>reflect</code></td><td>Trigger AI reflection on memories</td></tr>
<tr><td><code>dismiss_trigger</code></td><td>Dismiss a smart trigger without firing webhook</td></tr>
<tr><td><code>fix_entity_type</code></td><td>Fix entity type classification</td></tr>
<tr><td><code>list_memories</code></td><td>List all memory entities with types and fact counts</td></tr>
<tr><td><code>get_reflections</code></td><td>Get AI-generated reflections and insights</td></tr>
<tr><td><code>dedup</code></td><td>Find and merge duplicate entities automatically</td></tr>
<tr><td><code>checkpoint</code></td><td>Save session checkpoint with decisions and learnings</td></tr>
<tr><td><code>context_for</code></td><td>Get relevant context pack for a specific task</td></tr>
<tr><td><code>generate_rules_file</code></td><td>Generate CLAUDE.md / .cursorrules from memory</td></tr>
</table>

<h2>HTTP transport</h2>
<p>For remote/cloud MCP clients, Mengram also supports SSE transport:</p>
<pre><code>SSE endpoint: https://mengram.io/mcp/sse
Messages: https://mengram.io/mcp/messages/</code></pre>
""",
        },
        "n8n": {
            "title": "n8n Integration",
            "description": "Add persistent memory to any n8n AI agent workflow with a ready-to-import template.",
            "content": """
<h2>Overview</h2>
<p>Add long-term memory to any n8n AI workflow. Your agent remembers users across sessions &mdash; preferences, past conversations, resolved issues. No custom code needed, just HTTP Request nodes.</p>

<h2>Quick start</h2>
<ol>
<li><a href="https://github.com/alibaizhanov/mengram/tree/main/examples/n8n" target="_blank">Download the workflow</a> from GitHub</li>
<li>In n8n, go to <strong>Workflows &rarr; Import from File</strong></li>
<li>Add your Mengram API key as a Header Auth credential</li>
<li>Activate and test</li>
</ol>

<h2>How it works</h2>
<pre><code>Webhook &rarr; Search Memories &rarr; Build Prompt &rarr; AI Response &rarr; Save to Memory &rarr; Respond</code></pre>

<p>The workflow adds 3 HTTP Request nodes to any AI agent:</p>
<ol>
<li><strong>Search memories</strong> &mdash; POST to <code>/v1/search</code> with the user's message to find relevant past context</li>
<li><strong>AI Agent responds</strong> &mdash; system prompt includes retrieved memories, agent responds with full context</li>
<li><strong>Save new memories</strong> &mdash; POST to <code>/v1/add</code> to store the conversation. Mengram auto-extracts facts and deduplicates</li>
</ol>

<h2>Credential setup</h2>
<p>Create a <strong>Header Auth</strong> credential in n8n:</p>
<pre><code>Name: Mengram API Key
Header Name: Authorization
Header Value: Bearer om-your-api-key</code></pre>

<h2>API endpoints used</h2>
<table>
<tr><th>Node</th><th>Method</th><th>URL</th><th>Body</th></tr>
<tr><td>Search Memories</td><td>POST</td><td><code>https://mengram.io/v1/search</code></td><td><code>{{"query": "...", "user_id": "...", "limit": 5}}</code></td></tr>
<tr><td>Save to Memory</td><td>POST</td><td><code>https://mengram.io/v1/add</code></td><td><code>{{"messages": [...], "user_id": "..."}}</code></td></tr>
</table>

<h2>Swap the LLM</h2>
<p>The workflow uses OpenAI <code>gpt-4o-mini</code> by default. To use a different LLM, change the URL and body in the AI Response node:</p>
<ul>
<li><strong>Anthropic</strong>: <code>https://api.anthropic.com/v1/messages</code></li>
<li><strong>Ollama</strong> (local): <code>http://localhost:11434/api/chat</code></li>
<li><strong>Any OpenAI-compatible API</strong>: just change the URL and model name</li>
</ul>

<h2>Example</h2>
<pre><code>curl -X POST http://localhost:5678/webhook/chat \\
  -H "Content-Type: application/json" \\
  -d '{{"message": "I prefer Python and use Railway for hosting", "user_id": "user-123"}}'

# Later...
curl -X POST http://localhost:5678/webhook/chat \\
  -H "Content-Type: application/json" \\
  -d '{{"message": "What hosting should I deploy to?", "user_id": "user-123"}}'

# Agent remembers Railway preference and responds accordingly</code></pre>

<h2>Links</h2>
<ul>
<li><a href="https://github.com/alibaizhanov/mengram/tree/main/examples/n8n" target="_blank">Workflow on GitHub</a></li>
<li><a href="https://docs.mengram.io/api-reference">API Reference</a></li>
</ul>
""",
        },
        "api-reference": {
            "title": "API Reference",
            "description": "Complete REST API documentation for Mengram with all endpoints, parameters, and response formats.",
            "content": """
<h2>Base URL</h2>
<pre><code>https://mengram.io</code></pre>

<h2>Authentication</h2>
<p>All requests require a Bearer token in the Authorization header:</p>
<pre><code>Authorization: Bearer om-your-api-key</code></pre>

<h2>Core endpoints</h2>

<h3>POST /v1/add</h3>
<p>Add memories from a conversation.</p>
<pre><code>curl -X POST https://mengram.io/v1/add \\
  -H "Authorization: Bearer om-..." \\
  -H "Content-Type: application/json" \\
  -d '{{
    "messages": [
      {{"role": "user", "content": "I use Python and Railway"}},
      {{"role": "assistant", "content": "Noted."}}
    ],
    "user_id": "default"
  }}'</code></pre>
<p>Response: <code>{{"status": "accepted", "job_id": "job-..."}}</code></p>

<h3>POST /v1/add_text</h3>
<p>Add memories from plain text.</p>
<pre><code>{{"text": "Meeting notes: migrating to PostgreSQL 16", "user_id": "default"}}</code></pre>

<h3>POST /v1/search</h3>
<p>Semantic search across the knowledge graph.</p>
<pre><code>{{"query": "database preferences", "user_id": "default", "limit": 5, "graph_depth": 2}}</code></pre>

<h3>POST /v1/search/all</h3>
<p>Unified search across all 3 memory types.</p>
<pre><code>{{"query": "deployment", "user_id": "default", "limit": 5}}</code></pre>
<p>Response: <code>{{"semantic": [...], "episodic": [...], "procedural": [...]}}</code></p>

<h3>GET /v1/memories</h3>
<p>List all entities for a user.</p>

<h3>GET /v1/memory/:name</h3>
<p>Get details for a specific entity.</p>

<h3>DELETE /v1/memory/:name</h3>
<p>Delete an entity.</p>

<h2>Cognitive Profile</h2>

<h3>GET /v1/profile</h3>
<p>Generate a Cognitive Profile system prompt.</p>
<p>Query params: <code>force=true</code> to regenerate, <code>sub_user_id</code> for multi-user.</p>

<h2>Episodic Memory</h2>

<h3>GET /v1/episodes</h3>
<p>List recent episodes. Params: <code>limit</code>, <code>after</code>, <code>before</code>.</p>

<h3>GET /v1/episodes/search</h3>
<p>Search episodes. Params: <code>query</code>, <code>limit</code>, <code>after</code>, <code>before</code>.</p>

<h2>Procedural Memory</h2>

<h3>GET /v1/procedures</h3>
<p>List procedures. Params: <code>limit</code>.</p>

<h3>GET /v1/procedures/search</h3>
<p>Search procedures. Params: <code>query</code>, <code>limit</code>.</p>

<h3>PATCH /v1/procedures/:id/feedback</h3>
<p>Record success/failure. Params: <code>success=true|false</code>. Body: <code>{{"context": "...", "failed_at_step": 3}}</code></p>

<h3>GET /v1/procedures/:id/history</h3>
<p>Get version history for a procedure.</p>

<h2>Memory Management</h2>

<h3>POST /v1/dedup</h3>
<p>Find and merge duplicate entities.</p>

<h3>POST /v1/merge</h3>
<p>Merge two entities. Params: <code>source</code>, <code>target</code>.</p>

<h3>POST /v1/archive_fact</h3>
<p>Archive a specific fact. Body: <code>{{"entity_name": "...", "fact_content": "..."}}</code></p>

<h3>POST /v1/agents/run</h3>
<p>Run memory agents. Params: <code>agent=all|curator|connector|digest</code>, <code>auto_fix=true|false</code>.</p>

<h2>Jobs</h2>

<h3>GET /v1/jobs/:id</h3>
<p>Check status of a background job. Response: <code>{{"status": "completed|processing|failed", ...}}</code></p>

<h2>Webhooks</h2>

<h3>POST /v1/webhooks</h3>
<p>Create a webhook. Body: <code>{{"url": "...", "event_types": ["memory_add"]}}</code></p>

<h3>GET /v1/webhooks</h3>
<p>List all webhooks.</p>

<p>For interactive API docs, see <a href="/swagger">Swagger UI</a> or <a href="/redoc">ReDoc</a>.</p>
""",
        },
        "search-filters": {
            "title": "Search & Filters",
            "description": "Semantic search, metadata filters, graph traversal depth, and unified search across all memory types.",
            "content": """
<h2>Basic search</h2>
<pre><code>results = m.search("deployment stack")
# Returns top 5 entities by relevance</code></pre>

<h2>Parameters</h2>
<table>
<tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr>
<tr><td><code>query</code></td><td>str</td><td>required</td><td>Natural language search query</td></tr>
<tr><td><code>limit</code></td><td>int</td><td>5</td><td>Maximum results to return</td></tr>
<tr><td><code>graph_depth</code></td><td>int</td><td>2</td><td>How many hops to traverse in the knowledge graph</td></tr>
<tr><td><code>user_id</code></td><td>str</td><td>"default"</td><td>User whose memories to search</td></tr>
<tr><td><code>filters</code></td><td>dict</td><td>None</td><td>Metadata key-value filters</td></tr>
</table>

<h2>Metadata filters</h2>
<p>Filter search results by metadata stored on entities. Uses PostgreSQL JSONB containment (<code>@&gt;</code>) for fast filtering with GIN indexes.</p>
<pre><code># Filter by agent
results = m.search("config", filters={{"agent_id": "support-bot"}})

# Filter by app
results = m.search("preferences", filters={{"app_id": "prod"}})

# Multiple filters (AND logic)
results = m.search("issues", filters={{
    "agent_id": "support-bot",
    "app_id": "production",
}})

# Also works with shorthand parameters
results = m.search("config", agent_id="support-bot", app_id="prod")</code></pre>

<h2>Graph depth</h2>
<p>Controls how many relationship hops the search traverses. Higher values find more related context but take longer.</p>
<pre><code># Shallow — just direct matches
results = m.search("Python", graph_depth=0)

# Default — 2 hops (entity → related → related)
results = m.search("Python", graph_depth=2)

# Deep — traverse far connections
results = m.search("Python", graph_depth=4)</code></pre>

<h2>Unified search</h2>
<p>Search all 3 memory types in a single call:</p>
<pre><code>results = m.search_all("deployment problems")

# Semantic — knowledge graph entities with facts
for entity in results["semantic"]:
    print(entity["entity"], entity["facts"])

# Episodic — events and experiences
for event in results["episodic"]:
    print(event["summary"], event["outcome"])

# Procedural — workflows and processes
for proc in results["procedural"]:
    print(proc["name"], proc["steps"])</code></pre>

<h2>Timeline search</h2>
<p>Search facts by time range:</p>
<pre><code>facts = m.timeline(after="2026-01-01", before="2026-02-01")
for f in facts:
    print(f["created_at"], f["entity"], f["fact"])</code></pre>
""",
        },
        "webhooks": {
            "title": "Webhooks",
            "description": "Real-time notifications when memories are created, updated, or deleted.",
            "content": """
<h2>Overview</h2>
<p>Webhooks send HTTP POST requests to your server when memory events occur. Use them to sync memories with your app, trigger workflows, or build real-time features.</p>

<h2>Create a webhook</h2>
<pre><code>hook = m.create_webhook(
    url="https://your-app.com/webhooks/mengram",
    name="Production webhook",
    event_types=["memory_add", "memory_update", "memory_delete"],
    secret="your-hmac-secret",  # optional, for signature verification
)
print(hook)  # {{"id": 1, "url": "...", "active": true}}</code></pre>

<h2>Event types</h2>
<table>
<tr><th>Event</th><th>Description</th></tr>
<tr><td><code>memory_add</code></td><td>New entity or facts added</td></tr>
<tr><td><code>memory_update</code></td><td>Entity facts updated</td></tr>
<tr><td><code>memory_delete</code></td><td>Entity deleted</td></tr>
</table>

<h2>Webhook payload</h2>
<pre><code>{{
  "event": "memory_add",
  "timestamp": "2026-02-27T10:30:00Z",
  "data": {{
    "entity": "PostgreSQL",
    "type": "technology",
    "facts": ["Uses PostgreSQL 16", "Deployed on Railway"],
    "user_id": "default"
  }}
}}</code></pre>

<h2>Signature verification</h2>
<p>If you provided a <code>secret</code>, each request includes an <code>X-Mengram-Signature</code> header with an HMAC-SHA256 signature of the request body.</p>
<pre><code>import hmac, hashlib

def verify_webhook(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={{expected}}", signature)</code></pre>

<h2>Manage webhooks</h2>
<pre><code># List all
hooks = m.get_webhooks()

# Update
m.update_webhook(webhook_id=1, active=False)

# Delete
m.delete_webhook(webhook_id=1)</code></pre>
""",
        },
    }

    @app.get("/docs", response_class=RedirectResponse)
    async def docs_index():
        """Redirect to Mintlify docs."""
        return RedirectResponse("https://docs.mengram.io", status_code=301)

    @app.get("/docs/{slug}", response_class=RedirectResponse)
    async def docs_page(slug: str):
        """Redirect to Mintlify docs."""
        return RedirectResponse(f"https://docs.mengram.io/{slug}", status_code=301)

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
        sub = store.get_subscription(user_id)
        plan = sub.get("plan", "free") if sub else "free"
        return {
            "email": email,
            "plan": plan,
            "user_id": user_id,
        }

    @app.post("/v1/signup", tags=["System"])
    async def signup(req: SignupRequest, request: Request):
        """Step 1: Send verification code to email."""
        try:
            email = req.validated_email
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid email address")

        # Rate limit: 5/min per IP, 3/min per email
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(f"signup:{client_ip}", 5):
            raise HTTPException(status_code=429, detail="Too many signup attempts. Try again in 60 seconds.")
        if not _check_rate_limit(f"signup_email:{email}", 3):
            raise HTTPException(status_code=429, detail="Too many attempts for this email.")

        existing = store.get_user_by_email(email)
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

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

        user_id = store.create_user(email)
        api_key = store.create_api_key(user_id)
        _send_api_key_email(email, api_key, is_reset=False)

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

        new_key = store.reset_api_key(user_id)
        _send_api_key_email(email, new_key, is_reset=True)

        return SignupResponse(
            api_key=new_key,
            message="New API key generated. Old keys are now inactive."
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
        store.cache.set(f"github_state:{state}", "1", ttl=600)
        github_url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={GITHUB_CLIENT_ID}"
            f"&redirect_uri=https://mengram.io/auth/github/callback"
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
        if not store.cache.get(f"github_state:{state}"):
            return _github_error_page("Invalid or expired state. Please try again.")
        # Invalidate state by overwriting with short TTL
        store.cache.set(f"github_state:{state}", "", ttl=1)

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
        user_id = store.create_user(email)
        api_key = store.create_api_key(user_id, name="github-oauth")
        _send_api_key_email(email, api_key, is_reset=False)
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

<div class="btns">
<a class="btn-pri" href="/dashboard">Open Console</a>
<a class="btn-sec" href="https://docs.mengram.io/claude-code">Full Guide</a>
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

    # ---- OAuth (for ChatGPT Custom GPTs) ----

    @app.get("/oauth/authorize")
    async def oauth_authorize(
        client_id: str = "",
        redirect_uri: str = "",
        state: str = "",
        response_type: str = "code",
    ):
        """OAuth authorize page — shows email login."""
        from urllib.parse import quote
        redirect_uri_encoded = quote(redirect_uri, safe="")
        state_encoded = quote(state, safe="")
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
  <div class="logo"><svg width='32' height='32' viewBox='0 0 120 120'><path d='M60 16 Q92 16 96 48 Q100 78 72 88 Q50 96 38 76 Q26 58 46 46 Q62 38 70 52 Q76 64 62 68' fill='none' stroke='#a855f7' stroke-width='8' stroke-linecap='round'/><circle cx='62' cy='68' r='8' fill='#a855f7'/><circle cx='62' cy='68' r='3.5' fill='white'/></svg></div>
  <h1>Sign in to Mengram</h1>
  <p>Connect your memory to ChatGPT</p>

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
    body: JSON.stringify({{email, code, redirect_uri: redirectUri, state}})
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
            user_id = store.create_user(email)
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

        # Brute-force protection: 5 attempts/min per email, 20/min per IP
        if not _check_rate_limit(f"verify:{email}", 5):
            return {"error": "Too many attempts. Try again in 60 seconds."}
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(f"verify_ip:{client_ip}", 20):
            return {"error": "Too many attempts. Try again in 60 seconds."}

        if not store.verify_email_code(email, code):
            return {"error": "Invalid or expired code"}

        user_id = store.get_user_by_email(email)
        if not user_id:
            return {"error": "User not found"}

        # Validate redirect_uri — must be HTTPS or localhost
        if redirect_uri:
            from urllib.parse import urlparse
            parsed = urlparse(redirect_uri)
            if parsed.scheme not in ("https", "http"):
                return {"error": "Invalid redirect_uri scheme"}
            # Allow localhost for dev, require HTTPS for everything else
            if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
                return {"error": "redirect_uri must use HTTPS"}

        # Create OAuth authorization code
        oauth_code = secrets.token_urlsafe(32)
        store.save_oauth_code(oauth_code, user_id, redirect_uri, state)

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

        # Get or create API key for this user
        user_id = result["user_id"]
        api_key = store.create_api_key(user_id, name="chatgpt-oauth")

        return {
            "access_token": api_key,
            "token_type": "Bearer",
            "scope": "read write",
        }

    @app.get("/v1/health", tags=["System"])
    async def health(authorization: str = Header(None)):
        """Health check. Returns basic status for unauthenticated, detailed diagnostics for authenticated."""
        result = {"status": "ok", "version": "2.16.0"}

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

    # ---- Protected endpoints ----

    @app.post("/v1/add", tags=["Memory"])
    async def add(req: AddRequest, ctx: AuthContext = Depends(auth)):
        """
        Add memories from conversation.
        Returns immediately with job_id, processes in background.
        """
        user_id = ctx.user_id
        use_quota(ctx, "add")  # atomic check+increment before background processing
        import threading

        sub_uid = req.user_id or "default"

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
                            "upgrade_url": "https://mengram.io/#pricing",
                        })
        job_id = store.create_job(user_id, "add")
        # Build metadata from categories
        metadata = {}
        if req.agent_id:
            metadata["agent_id"] = req.agent_id
        if req.run_id:
            metadata["run_id"] = req.run_id
        if req.app_id:
            metadata["app_id"] = req.app_id

        def process_in_background():
            created = []
            try:
                extractor = get_llm()
                conversation = [{"role": m.role, "content": m.content} for m in req.messages]
                from concurrent.futures import ThreadPoolExecutor, as_completed

                # Get existing entities context for smarter extraction
                existing_context = ""
                try:
                    existing_context = store.get_existing_context(user_id, sub_user_id=sub_uid)
                except Exception as e:
                    logger.error(f"⚠️ Context fetch failed: {e}")

                # ---- Windowed extraction: extract per 4-message window ----
                WINDOW_SIZE = 4  # 2 user+assistant exchanges per window
                all_episodes = []
                all_procedures = []
                all_entities = []  # for smart triggers at end
                embedding_queue = []  # [(entity_id, chunks)]

                for win_start in range(0, max(len(conversation), 1), WINDOW_SIZE):
                    window = conversation[win_start:win_start + WINDOW_SIZE]
                    if not window:
                        break

                    win_extraction = extractor.extract(window, existing_context=existing_context)
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
                                plain_facts = [f.content if hasattr(f, 'content') else str(f)
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
                                fact_strings.append(f.content)
                                if f.event_date:
                                    fact_dates[f.content] = f.event_date
                            else:
                                fact_strings.append(str(f))

                        archived = conflict_results.get(name)
                        if archived:
                            store.fire_webhooks(user_id, "memory_update", {
                                "entity": name,
                                "archived_facts": archived,
                                "new_facts": fact_strings
                            })

                        entity_id = store.save_entity(
                            user_id=user_id,
                            name=name,
                            type=entity.entity_type,
                            facts=fact_strings,
                            relations=entity_relations,
                            knowledge=entity_knowledge,
                            metadata=metadata if metadata else None,
                            expires_at=req.expiration_date,
                            sub_user_id=sub_uid,
                            fact_dates=fact_dates,
                        )
                        created.append(name)

                        chunks = [name] + [f"{name}: {fs}" for fs in fact_strings]
                        for r in entity_relations:
                            target = r.get("target", "")
                            rel_type = r.get("type", "")
                            if target and rel_type:
                                chunks.append(f"{name} {rel_type} {target}")
                        for k in entity_knowledge:
                            chunks.append(f"{k['title']} {k['content']}")
                        embedding_queue.append((entity_id, chunks))

                    # -- Refresh context for next window (includes just-saved entities) --
                    if win_start + WINDOW_SIZE < len(conversation):
                        try:
                            existing_context = store.get_existing_context(
                                user_id, sub_user_id=sub_uid)
                        except Exception:
                            pass

                # ---- Batch embeddings across ALL windows (1 API call) ----
                embedder = get_embedder()
                if embedder and embedding_queue:
                    all_chunks = []
                    chunk_map = []
                    for entity_id, chunks in embedding_queue:
                        store.delete_embeddings(entity_id)
                        for chunk in chunks:
                            chunk_map.append((entity_id, chunk))
                            all_chunks.append(chunk)

                    if all_chunks:
                        all_embeddings = embedder.embed_batch(all_chunks)
                        for (entity_id, chunk_text), emb in zip(chunk_map, all_embeddings):
                            store.save_embedding(entity_id, chunk_text, emb)

                store.log_usage(user_id, "add")
                # increment_usage already done atomically in use_quota above

                # ---- Raw Conversation Chunk: save for fallback retrieval ----
                try:
                    chunk_text = "\n".join(
                        f"{m.get('role','user')}: {m.get('content','')}"
                        for m in conversation
                    )[:4000]  # cap at 4000 chars
                    chunk_id = store.save_conversation_chunk(
                        user_id, chunk_text, sub_user_id=sub_uid)
                    if embedder:
                        chunk_embs = embedder.embed_batch([chunk_text[:2000]])
                        if chunk_embs:
                            store.save_chunk_embedding(chunk_id, chunk_text[:2000], chunk_embs[0])
                except Exception as e:
                    logger.error(f"⚠️ Raw chunk save failed: {e}")

                # ---- Episodic Memory: save episodes ----
                episodes_created = 0
                episodes_linked = 0
                embedder = get_embedder()
                for ep in all_episodes:
                    if not ep.summary:
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
                            expires_at=req.expiration_date,
                            sub_user_id=sub_uid,
                            happened_at=getattr(ep, 'happened_at', None),
                        )
                        # Embed episode (truncate to 2000 chars for embedder safety)
                        ep_embedding = None
                        if embedder:
                            ep_text = f"{ep.summary}. {ep.context or ''} {ep.outcome or ''}"[:2000]
                            ep_embs = embedder.embed_batch([ep_text])
                            if ep_embs:
                                ep_embedding = ep_embs[0]
                                store.save_episode_embedding(episode_id, ep_text, ep_embedding)

                        # ---- Auto-link episode to existing procedure ----
                        if ep_embedding:
                            try:
                                from cloud.evolution import EvolutionEngine

                                similar_procs = store.search_procedures_vector(
                                    user_id, ep_embedding, top_k=3, sub_user_id=sub_uid)

                                # Combined scoring: vector + entity + keyword overlap
                                ep_text = f"{ep.summary}. {ep.context or ''} {ep.outcome or ''}"
                                best_proc = None
                                best_score = 0.0

                                for sp in (similar_procs or []):
                                    proc_text = f"{sp['name']}. {sp.get('trigger_condition') or ''}. "
                                    proc_text += "; ".join(
                                        s.get("action", "") for s in (sp.get("steps") or [])[:10]
                                    )
                                    score = EvolutionEngine.compute_link_score(
                                        vector_similarity=sp["score"],
                                        episode_participants=ep.participants or [],
                                        procedure_entity_names=sp.get("entity_names") or [],
                                        episode_text=ep_text,
                                        procedure_text=proc_text,
                                    )
                                    if score > best_score:
                                        best_score = score
                                        best_proc = sp

                                if best_proc and best_score >= 0.55:
                                    # Link episode to procedure
                                    store.link_episodes_to_procedure(
                                        [episode_id], best_proc["id"])

                                    is_failure = EvolutionEngine.is_failure_episode(
                                        ep.emotional_valence,
                                        outcome=ep.outcome or "",
                                        summary=ep.summary,
                                        context=ep.context or "",
                                    )
                                    if is_failure:
                                        # Failure → trigger evolution
                                        evo = EvolutionEngine(store, embedder, extractor.llm)
                                        evo_result = evo.evolve_on_failure(
                                            user_id, best_proc["id"], episode_id,
                                            ep.context or ep.summary,
                                            sub_user_id=sub_uid)
                                        if evo_result:
                                            logger.info(
                                                f"🔄 Auto-evolved '{best_proc['name']}' "
                                                f"v{evo_result['old_version']}→v{evo_result['new_version']} "
                                                f"from episode")
                                            # Create procedure_evolved trigger
                                            store.create_procedure_evolved_trigger(
                                                user_id=user_id,
                                                procedure_name=best_proc["name"],
                                                old_version=evo_result["old_version"],
                                                new_version=evo_result["new_version"],
                                                change_description=evo_result.get("change_description", ""),
                                                procedure_id=evo_result["new_procedure_id"],
                                                sub_user_id=sub_uid,
                                            )
                                            # Cross-procedure learning
                                            evo.suggest_cross_procedure_updates(
                                                user_id,
                                                evo_result["new_procedure_id"],
                                                evo_result.get("change_description", ""),
                                                sub_user_id=sub_uid,
                                            )
                                    else:
                                        # Success → increment success count
                                        store.procedure_feedback(
                                            user_id, best_proc["id"], success=True, sub_user_id=sub_uid)

                                    episodes_linked += 1
                            except Exception as e:
                                logger.error(f"⚠️ Episode auto-link failed: {e}")

                        episodes_created += 1
                    except Exception as e:
                        logger.error(f"⚠️ Episode save failed: {e}")

                # ---- Procedural Memory: save procedures ----
                procedures_created = 0
                for pr in all_procedures:
                    if not pr.name or not pr.steps:
                        continue
                    try:
                        proc_id = store.save_procedure(
                            user_id=user_id,
                            name=pr.name,
                            trigger_condition=pr.trigger,
                            steps=pr.steps,
                            entity_names=pr.entities,
                            metadata=metadata if metadata else None,
                            expires_at=req.expiration_date,
                            sub_user_id=sub_uid,
                        )
                        # Embed procedure
                        if embedder:
                            steps_summary = "; ".join(
                                s.get("action", "") for s in pr.steps[:10]
                            )
                            pr_text = f"{pr.name}. {pr.trigger or ''}. Steps: {steps_summary}"
                            pr_embs = embedder.embed_batch([pr_text])
                            if pr_embs:
                                store.delete_procedure_embeddings(proc_id)
                                store.save_procedure_embedding(proc_id, pr_text, pr_embs[0])
                        procedures_created += 1
                    except Exception as e:
                        logger.error(f"⚠️ Procedure save failed: {e}")

                # Invalidate search cache — fresh data available
                store.cache.invalidate(f"search:{user_id}:{sub_uid}")
                store.cache.invalidate(f"searchall:{user_id}:{sub_uid}")

                logger.info(f"✅ Background add complete for {user_id} "
                           f"(entities={len(created)}, episodes={episodes_created}, "
                           f"procedures={procedures_created}, linked={episodes_linked})")
                store.complete_job(job_id, {
                    "created": created,
                    "count": len(created),
                    "episodes": episodes_created,
                    "procedures": procedures_created,
                    "episodes_linked": episodes_linked,
                })

                # Auto-trigger reflection if needed (respects reflect quota)
                try:
                    if store.should_reflect(user_id, sub_user_id=sub_uid):
                        # Check reflect quota before consuming LLM resources
                        plan_quotas_local = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
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

                # ---- Smart Triggers: detect reminders, contradictions, patterns ----
                triggers_created = 0
                try:
                    triggers_created += store.detect_reminder_triggers(user_id, sub_user_id=sub_uid)
                    for entity in all_entities:
                        if entity.name and entity.facts:
                            plain_facts = [f.content if hasattr(f, 'content') else str(f)
                                           for f in entity.facts]
                            triggers_created += store.detect_contradiction_triggers(
                                user_id, plain_facts, entity.name, sub_user_id=sub_uid
                            )
                    triggers_created += store.detect_pattern_triggers(user_id, sub_user_id=sub_uid)
                    if triggers_created > 0:
                        logger.info(f"🧠 Smart triggers created: {triggers_created} for {user_id}")
                except Exception as e:
                    logger.error(f"⚠️ Smart triggers failed: {e}")

                # ---- Experience-Driven Procedures: detect patterns in episodes ----
                if episodes_created > 0:
                    try:
                        from cloud.evolution import EvolutionEngine
                        evo_engine = EvolutionEngine(store, embedder, extractor.llm)
                        evo_result = evo_engine.detect_and_create_from_episodes(user_id, sub_user_id=sub_uid)
                        if evo_result:
                            logger.info(f"🔄 Auto-created procedure '{evo_result['name']}' "
                                       f"from {evo_result['source_episode_count']} episodes")
                            # Notify user about auto-created procedure
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
            except Exception as e:
                logger.error(f"❌ Background add failed: {e}")
                store.fail_job(job_id, str(e))

        threading.Thread(target=process_in_background, daemon=True).start()

        from starlette.responses import JSONResponse
        return JSONResponse(status_code=202, content={
            "status": "accepted",
            "message": "Processing in background. Memories will appear shortly.",
            "job_id": job_id,
        })

    @app.post("/v1/add_text", tags=["Memory"])
    async def add_text(req: AddTextRequest, ctx: AuthContext = Depends(auth)):
        """Add memories from plain text (wraps into a single user message)."""
        add_req = AddRequest(
            messages=[Message(role="user", content=req.text)],
            user_id=req.user_id,
            agent_id=req.agent_id,
            run_id=req.run_id,
            app_id=req.app_id,
        )
        # Delegate to add() which handles quota check + increment internally
        result = await add(add_req, ctx)
        return result

    @app.get("/v1/jobs/{job_id}", tags=["System"])
    async def job_status(job_id: str, ctx: AuthContext = Depends(auth)):
        """Check status of a background job."""
        user_id = ctx.user_id
        job = store.get_job(job_id, user_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.post("/v1/search", tags=["Search"])
    async def search(req: SearchRequest, ctx: AuthContext = Depends(auth)):
        """Semantic search across memories with LLM re-ranking."""
        user_id = ctx.user_id
        use_quota(ctx, "search")  # atomic check+increment
        import hashlib as _hashlib

        sub_uid = req.user_id or "default"

        # Build metadata filters from explicit fields + filters dict
        meta_filters = dict(req.filters) if req.filters else {}
        if req.agent_id:
            meta_filters["agent_id"] = req.agent_id
        if req.run_id:
            meta_filters["run_id"] = req.run_id
        if req.app_id:
            meta_filters["app_id"] = req.app_id

        # ---- Redis cache: same query → instant response ----
        filter_str = json.dumps(meta_filters, sort_keys=True) if meta_filters else ""
        cache_key = f"search:{user_id}:{sub_uid}:{_hashlib.md5(f'{req.query}:{req.limit}:{req.graph_depth}:{filter_str}'.encode()).hexdigest()}"
        cached = store.cache.get(cache_key)
        if cached:
            store.log_usage(user_id, "search")
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
                results = store.search_vector_with_teams(user_id, emb, top_k=search_limit,
                                              query_text=req.query, graph_depth=req.graph_depth,
                                              sub_user_id=sub_uid, meta_filters=meta_filters)
                # Fallback: if nothing found, retry with lower threshold
                if not results:
                    results = store.search_vector_with_teams(user_id, emb, top_k=search_limit,
                                                  min_score=0.15, query_text=req.query,
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

        # Prepend matching reflections for richer context
        reflections = store.get_reflections(user_id, sub_user_id=sub_uid)
        if reflections:
            query_lower = req.query.lower()
            matching = [r for r in reflections if
                       query_lower in r["content"].lower() or
                       query_lower in r["title"].lower() or
                       any(w in r["content"].lower() for w in query_lower.split() if len(w) > 3)]
            if matching:
                # Add as a special "reflection" result at the top
                top_reflection = matching[0]
                results.insert(0, {
                    "entity": f"✨ Insight: {top_reflection['title']}",
                    "type": "reflection",
                    "scope": top_reflection["scope"],
                    "score": top_reflection["confidence"],
                    "facts": [top_reflection["content"]],
                    "relations": [],
                    "knowledge": [],
                })

        # Cache results in Redis (TTL 30s)
        store.cache.set(cache_key, results, ttl=30)
        store.log_usage(user_id, "search")
        # increment already done atomically in use_quota above

        return {"results": results}

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

            chunks = [name] + entity.get("facts", [])
            for r in entity.get("relations", []):
                target = r.get("target", "")
                rel_type = r.get("type", "")
                if target and rel_type:
                    chunks.append(f"{name} {rel_type} {target}")
            for k in entity.get("knowledge", []):
                chunks.append(f"{k.get('title', '')} {k.get('content', '')}")

            store.delete_embeddings(entity_id)
            embeddings = embedder.embed_batch(chunks)
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
        valid_types = {"person", "project", "technology", "company", "concept", "unknown"}
        if new_type not in valid_types:
            raise HTTPException(status_code=400, detail=f"Invalid type. Must be one of: {valid_types}")
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
        """Get all reflections. Optional ?scope=entity|cross|temporal"""
        user_id = ctx.user_id
        return {"reflections": store.get_reflections(user_id, scope=scope, sub_user_id=sub_user_id)}

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
            raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}. Use: curator, connector, digest, all")

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
                    "upgrade_url": "https://mengram.io/#pricing",
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
                    "upgrade_url": "https://mengram.io/#pricing",
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
        """Delete ALL memories (entities, facts, relations, knowledge). Irreversible."""
        user_id = ctx.user_id
        count = store.delete_all_entities(user_id, sub_user_id=sub_user_id)
        logger.warning(f"🗑️ DELETE ALL | user={user_id[:8]} | deleted={count} entities")
        return {"status": "deleted", "count": count}

    @app.get("/v1/stats", tags=["System"])
    async def stats(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Usage statistics."""
        user_id = ctx.user_id
        return store.get_stats(user_id, sub_user_id=sub_user_id)

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
        use_quota(ctx, "rules")  # profile uses LLM, shares quota with rules
        # force=true bypasses cache → LLM call, restrict to paid plans
        if force and ctx.plan == "free":
            force = False
        return store.get_profile(target_user_id, force=force, sub_user_id=sub_user_id)

    @app.get("/v1/profile", tags=["Memory"])
    async def get_own_profile(force: bool = False, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Cognitive Profile for the authenticated user."""
        user_id = ctx.user_id
        use_quota(ctx, "rules")  # profile uses LLM, shares quota with rules
        if force and ctx.plan == "free":
            force = False
        return store.get_profile(user_id, force=force, sub_user_id=sub_user_id)

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
        if force and ctx.plan == "free":
            force = False
        if force:
            store.cache.invalidate(f"rules:{user_id}:{sub_user_id}:{format}")
        return store.generate_rules_file(user_id, format=format, sub_user_id=sub_user_id)

    # ---- Episodic Memory ----

    @app.get("/v1/episodes", tags=["Episodic Memory"])
    async def list_episodes(
        limit: int = 20, after: str = None, before: str = None,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """List episodic memories (events, interactions, experiences)."""
        user_id = ctx.user_id
        episodes = store.get_episodes(user_id, limit=min(limit, 100),
                                       after=after, before=before, sub_user_id=sub_user_id)
        return {"episodes": episodes, "count": len(episodes)}

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
                user_id, emb, top_k=limit, after=after, before=before, sub_user_id=sub_user_id)
        else:
            results = store.search_episodes_text(user_id, query, top_k=limit, sub_user_id=sub_user_id)
        return {"results": results}

    # ---- Procedural Memory ----

    @app.get("/v1/procedures", tags=["Procedural Memory"])
    async def list_procedures(
        limit: int = 20,
        sub_user_id: str = Query("default"),
        ctx: AuthContext = Depends(auth)
    ):
        """List procedural memories (learned workflows, skills)."""
        user_id = ctx.user_id
        procedures = store.get_procedures(user_id, limit=min(limit, 100), sub_user_id=sub_user_id)
        return {"procedures": procedures, "count": len(procedures)}

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
            results = store.search_procedures_vector(user_id, emb, top_k=limit, sub_user_id=sub_user_id)
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
        user_id = ctx.user_id
        # Evolution on failure uses LLM + embedder — count as an add operation
        if not success and body and body.context:
            use_quota(ctx, "add")
        result = store.procedure_feedback(user_id, procedure_id, success, sub_user_id=sub_user_id)
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
        user_id = ctx.user_id
        history = store.get_procedure_history(user_id, procedure_id, sub_user_id=sub_user_id)
        if not history:
            raise HTTPException(status_code=404, detail="procedure not found")
        evolution = store.get_procedure_evolution(user_id, procedure_id, sub_user_id=sub_user_id)
        return {"versions": history, "evolution_log": evolution}

    @app.get("/v1/procedures/{procedure_id}/evolution", tags=["Procedural Memory"])
    async def procedure_evolution(procedure_id: str, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Get the evolution log for a procedure — what changed and why."""
        user_id = ctx.user_id
        evolution = store.get_procedure_evolution(user_id, procedure_id, sub_user_id=sub_user_id)
        return {"evolution": evolution}

    # ---- Unified Search (all 3 memory types) ----

    @app.post("/v1/search/all", tags=["Search"])
    async def search_all(req: SearchRequest, ctx: AuthContext = Depends(auth)):
        """Search across all memory types: semantic, episodic, and procedural.
        Returns categorized results from each memory system."""
        user_id = ctx.user_id
        use_quota(ctx, "search")  # atomic check+increment
        import hashlib as _hashlib

        sub_uid = req.user_id or "default"

        # Build metadata filters
        meta_filters = dict(req.filters) if req.filters else {}
        if req.agent_id:
            meta_filters["agent_id"] = req.agent_id
        if req.run_id:
            meta_filters["run_id"] = req.run_id
        if req.app_id:
            meta_filters["app_id"] = req.app_id

        # ---- Redis cache ----
        filter_str = json.dumps(meta_filters, sort_keys=True) if meta_filters else ""
        cache_key = f"searchall:{user_id}:{sub_uid}:{_hashlib.md5(f'{req.query}:{req.limit}:{req.graph_depth}:{filter_str}'.encode()).hexdigest()}"
        cached = store.cache.get(cache_key)
        if cached:
            store.log_usage(user_id, "search_all")
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
            semantic = store.search_vector_with_teams(
                user_id, emb, top_k=search_limit, query_text=req.query,
                graph_depth=req.graph_depth, sub_user_id=sub_uid, meta_filters=meta_filters)
            if not semantic:
                semantic = store.search_vector_with_teams(
                    user_id, emb, top_k=search_limit, min_score=0.15,
                    query_text=req.query, graph_depth=req.graph_depth, sub_user_id=sub_uid, meta_filters=meta_filters)
            # Episodic
            episodic = store.search_episodes_vector(
                user_id, emb, top_k=ep_limit, sub_user_id=sub_uid)
            # Procedural
            procedural = store.search_procedures_vector(
                user_id, emb, top_k=proc_limit, sub_user_id=sub_uid)
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

        result = {
            "semantic": semantic,
            "episodic": episodic,
            "procedural": procedural,
            "chunks": chunks,
        }

        # Cache in Redis (TTL 30s)
        store.cache.set(cache_key, result, ttl=30)
        store.log_usage(user_id, "search_all")
        # increment already done in use_quota above
        return result

    # ============================================
    # Smart Memory Triggers (v2.6)
    # ============================================

    @app.get("/v1/triggers", tags=["Smart Triggers"])
    async def get_own_triggers(include_fired: bool = False,
                               limit: int = 50, sub_user_id: str = Query("default"),
                               ctx: AuthContext = Depends(auth)):
        """Get smart triggers for the authenticated user."""
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

    # ---- Background trigger processing (cron) ----
    import threading, time as _time

    def _trigger_cron_loop():
        """Background thread that processes triggers every 5 minutes."""
        _time.sleep(30)  # Initial delay to let server start
        while True:
            try:
                result = store.process_all_triggers()
                if result["fired"] > 0:
                    logger.info(f"🧠 Trigger cron: fired {result['fired']} triggers")
            except Exception as e:
                logger.error(f"⚠️ Trigger cron error: {e}")
            _time.sleep(300)  # Every 5 minutes

    _cron_thread = threading.Thread(target=_trigger_cron_loop, daemon=True)
    _cron_thread.start()
    logger.info("🧠 Smart trigger cron started (every 5 min)")

    # ---- Background drip email cron ----

    def _drip_email_cron_loop():
        """Background thread that sends onboarding drip emails every 30 minutes."""
        _time.sleep(60)  # Initial delay
        while True:
            try:
                import secrets as _secrets

                # Completed signups with no API activity
                for user in store.get_inactive_completed_signups(24, "completed_24h"):
                    if store.try_record_drip(user["email"], "completed_24h", user["id"]):
                        _send_drip_email(user["email"], "completed_24h")

                for user in store.get_inactive_completed_signups(72, "completed_72h"):
                    if store.try_record_drip(user["email"], "completed_72h", user["id"]):
                        _send_drip_email(user["email"], "completed_72h")

                for user in store.get_inactive_completed_signups(168, "completed_7d"):
                    if store.try_record_drip(user["email"], "completed_7d", user["id"]):
                        _send_drip_email(user["email"], "completed_7d")

                # Incomplete signups (verification pending)
                for row in store.get_incomplete_signups_for_drip(1, "incomplete_1h"):
                    if store.try_record_drip(row["email"], "incomplete_1h"):
                        code = f"{_secrets.randbelow(900000) + 100000}"
                        store.save_email_code(row["email"], code)
                        _send_drip_email(row["email"], "incomplete_1h", code=code)

                for row in store.get_incomplete_signups_for_drip(24, "incomplete_24h"):
                    if store.try_record_drip(row["email"], "incomplete_24h"):
                        code = f"{_secrets.randbelow(900000) + 100000}"
                        store.save_email_code(row["email"], code)
                        _send_drip_email(row["email"], "incomplete_24h", code=code)

            except Exception as e:
                logger.error(f"⚠️ Drip email cron error: {e}")
            _time.sleep(1800)  # Every 30 minutes

    _drip_thread = threading.Thread(target=_drip_email_cron_loop, daemon=True)
    _drip_thread.start()
    logger.info("📧 Onboarding drip email cron started (every 30 min)")

    # ---- Billing & Subscription ----

    PADDLE_API_KEY = os.environ.get("PADDLE_API_KEY", "")
    PADDLE_WEBHOOK_SECRET = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
    PADDLE_ENV = os.environ.get("PADDLE_ENVIRONMENT", "sandbox")
    PADDLE_API_BASE = "https://api.paddle.com" if PADDLE_ENV == "production" else "https://sandbox-api.paddle.com"
    PADDLE_PRICES = {
        "pro": os.environ.get("PADDLE_PRICE_PRO", ""),
        "business": os.environ.get("PADDLE_PRICE_BUSINESS", ""),
    }

    def _paddle_request(method: str, path: str, body: dict = None) -> dict:
        """Make authenticated Paddle API request."""
        import urllib.request, urllib.error
        url = f"{PADDLE_API_BASE}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Authorization": f"Bearer {PADDLE_API_KEY}",
                "Content-Type": "application/json",
            }
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            logger.error(f"Paddle API error {e.code}: {err_body}")
            raise Exception(f"Paddle API {e.code}: {err_body}")

    @app.get("/v1/billing", tags=["Billing"])
    async def get_billing(ctx: AuthContext = Depends(auth)):
        """Current subscription plan, usage, and quotas."""
        user_id = ctx.user_id
        sub = store.get_subscription(user_id)
        usage = store.get_all_usage_counts(user_id)
        quotas = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
        return {
            "plan": ctx.plan,
            "status": sub.get("status", "active"),
            "current_period_end": sub.get("current_period_end"),
            "usage": usage,
            "quotas": {k: v for k, v in quotas.items() if k != "rate_limit"},
            "rate_limit": quotas["rate_limit"],
        }

    @app.post("/v1/billing/checkout", tags=["Billing"])
    async def create_checkout(plan: str = Query(..., pattern="^(pro|business)$"), ctx: AuthContext = Depends(auth)):
        """Create Paddle checkout transaction for plan upgrade. Returns checkout URL."""
        user_id = ctx.user_id
        if not PADDLE_API_KEY:
            raise HTTPException(status_code=503, detail="Billing not configured")
        price_id = PADDLE_PRICES.get(plan, "")
        if not price_id:
            raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

        # Build transaction request
        txn_body = {
            "items": [{"price_id": price_id, "quantity": 1}],
            "custom_data": {"mengram_user_id": user_id, "plan": plan},
        }

        # Attach existing Paddle customer if we have one
        sub = store.get_subscription(user_id)
        customer_id = sub.get("paddle_customer_id")
        if customer_id:
            txn_body["customer_id"] = customer_id

        try:
            result = _paddle_request("POST", "/transactions", txn_body)
            data = result.get("data", {})
            checkout_url = data.get("checkout", {}).get("url", "")
            transaction_id = data.get("id", "")
            if not checkout_url:
                raise HTTPException(status_code=502, detail="Paddle did not return checkout URL")
            return {"checkout_url": checkout_url, "transaction_id": transaction_id}
        except Exception as e:
            logger.error(f"Paddle checkout error: {e}")
            raise HTTPException(status_code=502, detail=f"Paddle error: {e}")

    @app.post("/v1/billing/portal", tags=["Billing"])
    async def create_portal(ctx: AuthContext = Depends(auth)):
        """Create Paddle customer portal session for managing subscription."""
        user_id = ctx.user_id
        if not PADDLE_API_KEY:
            raise HTTPException(status_code=503, detail="Billing not configured")

        sub = store.get_subscription(user_id)
        customer_id = sub.get("paddle_customer_id")
        if not customer_id:
            raise HTTPException(status_code=400, detail="No billing account. Subscribe first.")

        try:
            result = _paddle_request(
                "POST",
                f"/customers/{customer_id}/portal-sessions",
                {}
            )
            urls = result.get("data", {}).get("urls", {})
            overview_url = urls.get("general", {}).get("overview", "")
            if not overview_url:
                raise HTTPException(status_code=502, detail="Paddle did not return portal URL")
            return {"portal_url": overview_url}
        except Exception as e:
            logger.error(f"Paddle portal error: {e}")
            raise HTTPException(status_code=502, detail=f"Paddle error: {e}")

    @app.post("/webhooks/paddle", tags=["Billing"])
    async def paddle_webhook(request: Request):
        """Paddle webhook handler. No auth — verified by HMAC signature."""
        if not PADDLE_WEBHOOK_SECRET:
            raise HTTPException(status_code=503, detail="Billing not configured")

        import hmac, hashlib

        raw_body = await request.body()
        sig_header = request.headers.get("Paddle-Signature", "")

        # Parse ts=...;h1=... from header
        sig_parts = {}
        for part in sig_header.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                sig_parts[k] = v

        ts = sig_parts.get("ts", "")
        h1 = sig_parts.get("h1", "")
        if not ts or not h1:
            raise HTTPException(status_code=400, detail="Invalid Paddle-Signature")

        # Verify HMAC-SHA256
        signed_payload = f"{ts}:{raw_body.decode('utf-8')}"
        computed = hmac.new(
            PADDLE_WEBHOOK_SECRET.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(computed, h1):
            raise HTTPException(status_code=400, detail="Invalid signature")

        event = json.loads(raw_body)
        event_type = event.get("event_type", "")
        data = event.get("data", {})

        if event_type == "transaction.completed":
            # Save customer_id → user mapping early (before subscription events)
            custom = data.get("custom_data", {})
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            if user_id and customer_id:
                store.update_subscription(user_id, paddle_customer_id=customer_id)
                logger.info(f"Payment completed: user={user_id} customer={customer_id}")

        elif event_type == "subscription.activated":
            custom = data.get("custom_data") or {}
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            subscription_id = data.get("id", "")

            if not user_id and customer_id:
                user_id = store.get_user_by_paddle_customer(customer_id)

            # Detect plan from custom_data or items price_id
            plan = custom.get("plan")
            if not plan:
                items = data.get("items", [])
                if items:
                    price_id = items[0].get("price", {}).get("id", "")
                    if price_id == PADDLE_PRICES.get("business"):
                        plan = "business"
                    elif price_id == PADDLE_PRICES.get("pro"):
                        plan = "pro"
            if not plan:
                plan = "pro"

            if user_id:
                updates = {
                    "plan": plan,
                    "status": "active",
                    "paddle_customer_id": customer_id,
                    "paddle_subscription_id": subscription_id,
                }
                current_period = data.get("current_billing_period", {})
                if current_period.get("starts_at"):
                    updates["current_period_start"] = current_period["starts_at"]
                if current_period.get("ends_at"):
                    updates["current_period_end"] = current_period["ends_at"]
                store.update_subscription(user_id, **updates)
                logger.info(f"Subscription activated: user={user_id} plan={plan}")
            else:
                logger.error(f"Subscription activated but no user found: customer={customer_id}")

        elif event_type == "subscription.canceled":
            custom = data.get("custom_data", {})
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            if not user_id and customer_id:
                user_id = store.get_user_by_paddle_customer(customer_id)
            if user_id:
                store.update_subscription(user_id, plan="free", status="canceled")
                logger.info(f"Subscription canceled: user={user_id}")

        elif event_type == "subscription.past_due":
            custom = data.get("custom_data", {})
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            if not user_id and customer_id:
                user_id = store.get_user_by_paddle_customer(customer_id)
            if user_id:
                store.update_subscription(user_id, status="past_due")
                logger.warning(f"Payment past due: user={user_id}")

        elif event_type == "subscription.updated":
            # Handle plan changes (upgrade/downgrade) and status updates
            custom = data.get("custom_data", {})
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            if not user_id and customer_id:
                user_id = store.get_user_by_paddle_customer(customer_id)
            if user_id:
                updates = {"status": data.get("status", "active")}
                # Detect plan from items → price_id
                items = data.get("items", [])
                if items:
                    price_id = items[0].get("price", {}).get("id", "")
                    if price_id == PADDLE_PRICES.get("business"):
                        updates["plan"] = "business"
                    elif price_id == PADDLE_PRICES.get("pro"):
                        updates["plan"] = "pro"
                current_period = data.get("current_billing_period", {})
                if current_period.get("starts_at"):
                    updates["current_period_start"] = current_period["starts_at"]
                if current_period.get("ends_at"):
                    updates["current_period_end"] = current_period["ends_at"]
                store.update_subscription(user_id, **updates)
                logger.info(f"Subscription updated: user={user_id} updates={updates}")

        return {"received": True}

    # ---- MCP over HTTP (SSE transport for Smithery / remote MCP clients) ----

    # ---- MCP Server Card (for Smithery discovery) ----

    @app.get("/.well-known/mcp/server-card.json")
    async def mcp_server_card():
        return {
            "serverInfo": {
                "name": "mengram",
                "title": "Mengram — AI Memory Layer",
                "version": "2.16.0",
                "description": "Give AI agents memory that actually learns. 3 memory types: semantic (facts & preferences), episodic (events & decisions), and procedural (workflows that evolve from failures). Cognitive Profile, Smart Triggers, Memory Agents, Knowledge Graph. Free cloud API.",
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
                {"name": "fix_entity_type", "description": "Fix an entity's type classification (person, project, technology, company, concept, unknown).",
                 "annotations": {"title": "Fix Entity Type", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
                 "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Entity name to reclassify"}, "new_type": {"type": "string", "enum": ["person", "project", "technology", "company", "concept", "unknown"], "description": "Correct entity type"}}, "required": ["name", "new_type"]}},
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
        from api.cloud_mcp_server import create_cloud_mcp_server as _create_mcp
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

            base = os.environ.get("MENGRAM_URL", "https://mengram.io")
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
