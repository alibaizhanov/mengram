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
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mengram")

from fastapi import FastAPI, HTTPException, Depends, Header, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
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
    "free":     {"adds": 100,   "searches": 500,    "agents": 5,   "reflects": 5,   "dedups": 2,   "reindexes": 2,   "rate_limit": 30,  "webhooks": 0,  "teams": 0,  "sub_users": 3},
    "pro":      {"adds": 1_000, "searches": 10_000, "agents": 50,  "reflects": 30,  "dedups": 20,  "reindexes": 10,  "rate_limit": 120, "webhooks": 10, "teams": 5,  "sub_users": 50},
    "business": {"adds": 5_000, "searches": 30_000, "agents": -1,  "reflects": -1,  "dedups": -1,  "reindexes": -1,  "rate_limit": 300, "webhooks": 50, "teams": -1, "sub_users": -1},
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
- **OpenClaw** — plugin with auto-recall/capture hooks, 6 tools, and Graph RAG across all channels

### Authentication
All endpoints require `Authorization: Bearer YOUR_API_KEY` header.

### Quick Start
```python
from cloud.client import CloudMemory
m = CloudMemory(api_key="om-...")
m.add([{"role": "user", "content": "I use Python and Railway"}])
results = m.search_all("deployment")  # semantic + episodic + procedural
profile = m.get_profile()             # instant system prompt
```
        """,
        version="2.14.5",
        docs_url="/docs",
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    store = CloudStore(DATABASE_URL, pool_min=1, pool_max=2, redis_url=REDIS_URL)

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

    # ---- Re-ranking (Cohere Rerank → gpt-4o-mini fallback) ----
    _cohere_client = None
    _openai_rerank_client = None

    def rerank_results(query: str, results: list[dict], plan: str = "business") -> list[dict]:
        """Re-rank search results based on subscription plan.
        Free: no reranking.  Pro: gpt-4o-mini.  Business: Cohere Rerank → gpt-4o-mini fallback."""
        if not results or len(results) <= 1:
            return results

        # Free plan: no reranking — return raw vector results
        if plan == "free":
            return results

        # Try Cohere Rerank first — fact-level (cross-encoder, more precise)
        # Only for Business plan (Pro skips straight to gpt-4o-mini)
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

        # Fallback: gpt-4o-mini
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
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0,
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

    def check_quota(ctx: AuthContext, action: str):
        """Check if user has quota remaining. Raises HTTP 402 if exceeded.
        Note: prefer use_quota() which atomically checks AND increments."""
        quota_map = {
            "add": "adds", "search": "searches", "agent": "agents",
            "reflect": "reflects", "dedup": "dedups", "reindex": "reindexes",
        }
        quota_key = quota_map.get(action)
        if not quota_key:
            return
        plan_quotas = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
        max_allowed = plan_quotas.get(quota_key, 0)
        if max_allowed == -1:
            return  # unlimited
        current = store.get_usage_count(ctx.user_id, action)
        if current >= max_allowed:
            _raise_quota_error(action, max_allowed, current, ctx.plan)

    def use_quota(ctx: AuthContext, action: str, count: int = 1):
        """Atomically check quota AND increment usage in one operation.
        Prevents race conditions where concurrent requests bypass limits."""
        quota_map = {
            "add": "adds", "search": "searches", "agent": "agents",
            "reflect": "reflects", "dedup": "dedups", "reindex": "reindexes",
        }
        quota_key = quota_map.get(action)
        if not quota_key:
            return
        plan_quotas = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
        max_allowed = plan_quotas.get(quota_key, 0)
        try:
            store.check_and_increment(ctx.user_id, action, max_allowed, count)
        except ValueError as e:
            parts = str(e).split(":")
            if parts[0] == "quota_exceeded":
                current = int(parts[2]) if len(parts) > 2 else 0
                limit = int(parts[3]) if len(parts) > 3 else max_allowed
                _raise_quota_error(action, limit, current, ctx.plan)
            raise

    def _raise_quota_error(action, max_allowed, current, plan):
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
                detail=f"Rate limit exceeded ({rate_limit} requests/min). Retry in 60 seconds."
            )
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

            html = f"""
            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:520px;margin:0 auto;padding:40px 24px;color:#e8e8f0;background:#0a0a12;border-radius:16px">
                <div style="text-align:center;margin-bottom:32px">
                    <svg width="36" height="36" viewBox="0 0 120 120"><path d="M60 16 Q92 16 96 48 Q100 78 72 88 Q50 96 38 76 Q26 58 46 46 Q62 38 70 52 Q76 64 62 68" fill="none" stroke="#a855f7" stroke-width="8" stroke-linecap="round"/><circle cx="62" cy="68" r="8" fill="#a855f7"/><circle cx="62" cy="68" r="3.5" fill="white"/></svg>
                    <h1 style="font-size:22px;font-weight:700;margin:8px 0 4px;color:#e8e8f0">Mengram</h1>
                    <p style="color:#8888a8;font-size:14px;margin:0">AI memory layer for apps</p>
                </div>
                <p style="font-size:15px;color:#c8c8d8;line-height:1.6">
                    {"Your API key has been reset. Old keys are now deactivated." if is_reset else "Welcome! Your account has been created."}
                </p>
                <div style="background:#12121e;border:1px solid #1a1a2e;border-radius:10px;padding:18px;margin:20px 0;text-align:center">
                    <p style="color:#8888a8;font-size:12px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px">Your API Key</p>
                    <code style="font-size:14px;color:#a78bfa;word-break:break-all">{api_key}</code>
                </div>
                <p style="font-size:13px;color:#ef4444;font-weight:600">⚠️ Save this key — it won't be shown again.</p>
                <p style="font-size:14px;color:#8888a8;margin-top:24px">
                    Quick start:<br>
                    <code style="color:#22c55e;font-size:13px">pip install mengram-ai</code>
                </p>
                <hr style="border:none;border-top:1px solid #1a1a2e;margin:28px 0">
                <p style="font-size:12px;color:#55556a;text-align:center">
                    <a href="https://mengram.io/dashboard" style="color:#7c3aed;text-decoration:none">Dashboard</a> ·
                    <a href="https://mengram.io/docs" style="color:#7c3aed;text-decoration:none">API Docs</a> ·
                    <a href="https://github.com/alibaizhanov/mengram" style="color:#7c3aed;text-decoration:none">GitHub</a>
                </p>
            </div>
            """

            resend.Emails.send({
                "from": EMAIL_FROM,
                "to": [email],
                "subject": subject,
                "html": html,
            })
            logger.info(f"📧 Email sent to {email} (key {action})")
        except Exception as e:
            logger.error(f"⚠️  Email send failed: {e}")

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

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        """Web dashboard."""
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

    @app.post("/v1/signup", tags=["System"], response_model=SignupResponse)
    async def signup(req: SignupRequest, request: Request):
        """Create account and get API key."""
        # Validate email format
        try:
            email = req.validated_email
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid email address")

        # Rate limit signups: 5/min per IP
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(f"signup:{client_ip}", 5):
            raise HTTPException(status_code=429, detail="Too many signup attempts. Try again in 60 seconds.")
        existing = store.get_user_by_email(email)
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        user_id = store.create_user(email)
        api_key = store.create_api_key(user_id)

        # Send key via email
        _send_api_key_email(email, api_key, is_reset=False)

        return SignupResponse(
            api_key=api_key,
            message="API key sent to your email. Save it — it won't be shown again."
        )

    @app.post("/v1/reset-key", tags=["System"])
    async def reset_key(req: ResetKeyRequest, request: Request):
        """Reset API key and send new one to email."""
        try:
            email = req.validated_email
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid email address")

        # Rate limit reset: 3/min per IP
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(f"reset:{client_ip}", 3):
            raise HTTPException(status_code=429, detail="Too many reset attempts. Try again in 60 seconds.")
        user_id = store.get_user_by_email(email)
        if not user_id:
            # Don't reveal whether email exists
            return {"message": "If this email is registered, a new API key has been sent."}

        new_key = store.reset_api_key(user_id)
        _send_api_key_email(email, new_key, is_reset=True)

        return {"message": "If this email is registered, a new API key has been sent."}

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
        result = {"status": "ok", "version": "2.14.5"}

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

        return {
            "status": "accepted",
            "message": "Processing in background. Memories will appear shortly.",
            "job_id": job_id,
        }

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
        # ---- Redis cache: same query → instant response ----
        cache_key = f"search:{user_id}:{sub_uid}:{_hashlib.md5(f'{req.query}:{req.limit}:{req.graph_depth}'.encode()).hexdigest()}"
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
                                              sub_user_id=sub_uid)
                # Fallback: if nothing found, retry with lower threshold
                if not results:
                    results = store.search_vector_with_teams(user_id, emb, top_k=search_limit,
                                                  min_score=0.15, query_text=req.query,
                                                  graph_depth=req.graph_depth,
                                                  sub_user_id=sub_uid)
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
        entities = store.get_all_entities(user_id, sub_user_id=sub_user_id)
        total = len(entities)
        entities = entities[offset:offset + limit]
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
        """
        user_id = ctx.user_id
        use_quota(ctx, "agent")  # atomic check+increment
        llm = get_llm()

        if agent == "all":
            result = store.run_all_agents(user_id, llm.llm, auto_fix=auto_fix, sub_user_id=sub_user_id)
            return {"status": "completed", "agents": result}
        elif agent == "curator":
            result = store.run_curator_agent(user_id, llm.llm, auto_fix=auto_fix, sub_user_id=sub_user_id)
            return {"status": "completed", "agent": "curator", "result": result}
        elif agent == "connector":
            result = store.run_connector_agent(user_id, llm.llm, sub_user_id=sub_user_id)
            return {"status": "completed", "agent": "connector", "result": result}
        elif agent == "digest":
            result = store.run_digest_agent(user_id, llm.llm, sub_user_id=sub_user_id)
            return {"status": "completed", "agent": "digest", "result": result}
        else:
            raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}. Use: curator, connector, digest, all")

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

    @app.get("/v1/stats", tags=["System"])
    async def stats(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Usage statistics."""
        user_id = ctx.user_id
        return store.get_stats(user_id, sub_user_id=sub_user_id)

    @app.get("/v1/graph", tags=["Memory"])
    async def graph(sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Knowledge graph for visualization."""
        user_id = ctx.user_id
        return store.get_graph(user_id, sub_user_id=sub_user_id)

    @app.get("/v1/feed", tags=["Memory"])
    async def feed(limit: int = 50, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Memory feed — recent facts with timestamps for dashboard."""
        user_id = ctx.user_id
        return store.get_feed(user_id, limit=min(limit, 100), sub_user_id=sub_user_id)

    @app.get("/v1/profile/{target_user_id}", tags=["Memory"])
    async def get_profile(target_user_id: str, force: bool = False, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Cognitive Profile — generates a ready-to-use system prompt from user memory.

        Returns a personalization prompt that can be inserted into any LLM.
        Cached for 1 hour. Use force=true to regenerate (Pro+ only)."""
        user_id = ctx.user_id
        if target_user_id != user_id:
            raise HTTPException(status_code=403, detail="Cannot access another user's profile")
        # force=true bypasses cache → LLM call, restrict to paid plans
        if force and ctx.plan == "free":
            force = False
        return store.get_profile(target_user_id, force=force, sub_user_id=sub_user_id)

    @app.get("/v1/profile", tags=["Memory"])
    async def get_own_profile(force: bool = False, sub_user_id: str = Query("default"), ctx: AuthContext = Depends(auth)):
        """Cognitive Profile for the authenticated user."""
        user_id = ctx.user_id
        if force and ctx.plan == "free":
            force = False
        return store.get_profile(user_id, force=force, sub_user_id=sub_user_id)

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
        # ---- Redis cache ----
        cache_key = f"searchall:{user_id}:{sub_uid}:{_hashlib.md5(f'{req.query}:{req.limit}:{req.graph_depth}'.encode()).hexdigest()}"
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
                graph_depth=req.graph_depth, sub_user_id=sub_uid)
            if not semantic:
                semantic = store.search_vector_with_teams(
                    user_id, emb, top_k=search_limit, min_score=0.15,
                    query_text=req.query, graph_depth=req.graph_depth, sub_user_id=sub_uid)
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
    async def create_checkout(plan: str = Query(..., regex="^(pro|business)$"), ctx: AuthContext = Depends(auth)):
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
    logger.info(f"   Docs: http://localhost:{port}/docs")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
