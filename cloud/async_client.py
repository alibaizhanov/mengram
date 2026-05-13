"""
Mengram Async Cloud Client.

Usage:
    from mengram import AsyncMengram

    m = AsyncMengram(api_key="om-...")

    # Add memories
    result = await m.add([{"role": "user", "content": "Deployed on Railway"}])

    # Search
    results = await m.search("deployment")

    # Unified search across all 3 memory types
    all_results = await m.search_all("deployment issues")

    # Cognitive Profile
    profile = await m.get_profile()
"""
from __future__ import annotations

import json
import os
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

# Cloudflare and similar gateways reject the default Python UA.
# See GitHub issue #31.
try:
    from importlib.metadata import version as _pkg_version
    _SDK_VERSION = _pkg_version("mengram-ai")
except Exception:
    _SDK_VERSION = "unknown"

_USER_AGENT = f"Mengram-Python-SDK/{_SDK_VERSION}"


class QuotaExceededError(Exception):
    """Raised when API quota is exceeded (HTTP 402)."""
    def __init__(self, detail: dict[str, Any]) -> None:
        self.action = detail.get("action", "unknown")
        self.limit = detail.get("limit", 0)
        self.current = detail.get("used", 0)
        self.plan = detail.get("plan", "free")
        super().__init__(
            f"Quota exceeded for '{self.action}': {self.current}/{self.limit} "
            f"(plan: {self.plan}). Upgrade at https://mengram.io/dashboard"
        )


class AsyncCloudMemory:
    """Async Mengram Cloud client. Uses httpx for non-blocking HTTP."""

    DEFAULT_BASE_URL = "https://mengram.io"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        if httpx is None:
            raise ImportError(
                "httpx is required for async client. "
                "Install with: pip install httpx"
            )
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def quota(self) -> dict[str, Any]:
        """Quota usage from last API response headers.
        Returns e.g. {"add": {"used": 5, "limit": 30}, "search": {"used": 12, "limit": 100}}
        """
        h = getattr(self, '_last_headers', {})
        result: dict[str, Any] = {}
        for action in ("add", "search"):
            prefix = f"X-Quota-{action.capitalize()}"
            used = h.get(f"{prefix}-Used")
            limit = h.get(f"{prefix}-Limit")
            if used is not None and limit is not None:
                result[action] = {"used": int(used), "limit": int(limit)}
        return result

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": _USER_AGENT,
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncCloudMemory:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _request(self, method: str, path: str, data: dict[str, Any] | None = None,
                       params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make authenticated API request with retry."""
        import asyncio

        client = self._get_client()
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.request(
                    method, path,
                    json=data,
                    params=clean_params if clean_params else None,
                )
                self._last_headers = resp.headers
                if resp.status_code == 402:
                    detail = resp.json().get("detail", {})
                    if isinstance(detail, dict):
                        raise QuotaExceededError(detail)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    last_err = e
                    continue
                try:
                    detail = e.response.json().get("detail", e.response.text)
                except Exception:
                    detail = e.response.text
                raise Exception(f"API error {e.response.status_code}: {detail}")
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    last_err = e
                    continue
                raise Exception(f"Network error: {e}")
        raise Exception(f"Request failed after 3 attempts: {last_err}")

    # ---- Core ----

    async def add(self, messages: list[dict[str, Any]], user_id: str = "default",
                  agent_id: str | None = None, run_id: str | None = None, app_id: str | None = None,
                  expiration_date: str | None = None,
                  source: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Add memories from conversation. Returns immediately — processing in background."""
        body: dict[str, Any] = {"messages": messages, "user_id": user_id}
        if agent_id: body["agent_id"] = agent_id
        if run_id: body["run_id"] = run_id
        if app_id: body["app_id"] = app_id
        if expiration_date: body["expiration_date"] = expiration_date
        if source: body["source"] = source
        if metadata: body["metadata"] = metadata
        return await self._request("POST", "/v1/add", body)

    async def add_text(self, text: str, user_id: str = "default",
                       agent_id: str | None = None, run_id: str | None = None,
                       app_id: str | None = None, expiration_date: str | None = None,
                       source: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Add memories from plain text."""
        body: dict[str, Any] = {"text": text, "user_id": user_id}
        if agent_id: body["agent_id"] = agent_id
        if run_id: body["run_id"] = run_id
        if app_id: body["app_id"] = app_id
        if expiration_date: body["expiration_date"] = expiration_date
        if source: body["source"] = source
        if metadata: body["metadata"] = metadata
        return await self._request("POST", "/v1/add_text", body)

    async def add_file(self, file_path: str, user_id: str = "default",
                       agent_id: str | None = None, run_id: str | None = None,
                       app_id: str | None = None) -> dict[str, Any]:
        """Upload a file (PDF, DOCX, TXT, MD) and extract memories.

        Uses vision AI for PDFs (two-pass extraction). Each page/chunk
        counts as 1 add from your quota. Returns immediately with job_id.

        Args:
            file_path: Path to file (.pdf, .docx, .txt, .md)
            user_id: User identifier
            agent_id: Agent identifier
            run_id: Run/session identifier
            app_id: Application identifier
        """
        import asyncio

        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_data = f.read()

        fields: dict[str, str] = {"user_id": user_id}
        if agent_id:
            fields["agent_id"] = agent_id
        if run_id:
            fields["run_id"] = run_id
        if app_id:
            fields["app_id"] = app_id

        client = self._get_client()
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.post(
                    "/v1/add_file",
                    files={"file": (filename, file_data, "application/octet-stream")},
                    data=fields,
                )
                if resp.status_code == 402:
                    detail = resp.json().get("detail", {})
                    if isinstance(detail, dict):
                        raise QuotaExceededError(detail)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    last_err = e
                    continue
                try:
                    detail = e.response.json().get("detail", e.response.text)
                except Exception:
                    detail = e.response.text
                raise Exception(f"API error {e.response.status_code}: {detail}")
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    last_err = e
                    continue
                raise Exception(f"Network error: {e}")
        raise Exception(f"Request failed after 3 attempts: {last_err}")

    async def search(self, query: str, user_id: str = "default",
                     limit: int = 5, agent_id: str | None = None,
                     run_id: str | None = None, app_id: str | None = None,
                     graph_depth: int = 2,
                     filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Semantic search across memories."""
        body: dict[str, Any] = {"query": query, "user_id": user_id, "limit": limit,
                "graph_depth": graph_depth}
        if agent_id: body["agent_id"] = agent_id
        if run_id: body["run_id"] = run_id
        if app_id: body["app_id"] = app_id
        if filters: body["filters"] = filters
        result = await self._request("POST", "/v1/search", body)
        return result.get("results", [])

    async def search_all(self, query: str, limit: int = 5,
                         user_id: str = "default",
                         graph_depth: int = 2) -> dict[str, Any]:
        """Search across all 3 memory types: semantic, episodic, procedural."""
        return await self._request("POST", "/v1/search/all",
                                   data={"query": query, "limit": limit,
                                         "user_id": user_id, "graph_depth": graph_depth})

    async def ask(self, query: str, user_id: str = "default",
                  max_facts: int = 15) -> dict[str, Any]:
        """
        Ask your memory a question — synthesized answer with citations.

        Premium (Pro / Growth / Business). Counts as 1 search against quota.
        See sync `CloudMemory.ask` for full docs.

        Returns:
            {"answer": str, "citations": [...], "facts_used": int}
        """
        return await self._request(
            "POST", "/v1/ask",
            data={"query": query, "user_id": user_id, "max_facts": max_facts},
        )

    async def get_all(self, user_id: str = "default") -> list[dict[str, Any]]:
        """Get all memories for user."""
        params: dict[str, Any] = {}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        result = await self._request("GET", "/v1/memories", params=params)
        return result.get("memories", [])

    async def get(self, name: str, user_id: str = "default") -> dict[str, Any] | None:
        """Get specific entity details."""
        import urllib.parse
        try:
            params: dict[str, Any] = {}
            if user_id and user_id != "default":
                params["sub_user_id"] = user_id
            return await self._request("GET", f"/v1/memory/{urllib.parse.quote(name, safe='')}", params=params)
        except Exception:
            return None

    async def delete(self, name: str, user_id: str = "default") -> bool:
        """Delete a memory."""
        import urllib.parse
        try:
            params: dict[str, Any] = {}
            if user_id and user_id != "default":
                params["sub_user_id"] = user_id
            await self._request("DELETE", f"/v1/memory/{urllib.parse.quote(name, safe='')}", params=params)
            return True
        except Exception:
            return False

    async def stats(self, user_id: str = "default") -> dict[str, Any]:
        """Get usage statistics."""
        params: dict[str, Any] = {}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("GET", "/v1/stats", params=params)

    # ---- Cognitive Profile ----

    async def get_profile(self, user_id: str = "default", force: bool = False) -> dict[str, Any]:
        """Generate a Cognitive Profile — ready-to-use system prompt from memory."""
        params: dict[str, Any] = {}
        if force: params["force"] = "true"
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("GET", "/v1/profile", params=params)

    # ---- Episodic Memory ----

    async def episodes(self, query: str | None = None, limit: int | None = None,
                       after: str | None = None, before: str | None = None,
                       user_id: str = "default") -> list[dict[str, Any]]:
        """Get or search episodic memories."""
        if query:
            params: dict[str, Any] = {"query": query, "limit": limit or 5}
            if after: params["after"] = after
            if before: params["before"] = before
            if user_id and user_id != "default":
                params["sub_user_id"] = user_id
            resp = await self._request("GET", "/v1/episodes/search", params=params)
            return resp.get("results", [])
        else:
            params = {"limit": limit or 20}
            if after: params["after"] = after
            if before: params["before"] = before
            if user_id and user_id != "default":
                params["sub_user_id"] = user_id
            resp = await self._request("GET", "/v1/episodes", params=params)
            return resp.get("episodes", [])

    # ---- Procedural Memory ----

    async def procedures(self, query: str | None = None, limit: int = 20,
                         user_id: str = "default") -> list[dict[str, Any]]:
        """Get or search procedural memories."""
        if query:
            params: dict[str, Any] = {"query": query, "limit": limit}
            if user_id and user_id != "default":
                params["sub_user_id"] = user_id
            resp = await self._request("GET", "/v1/procedures/search", params=params)
            return resp.get("results", [])
        else:
            params = {"limit": limit}
            if user_id and user_id != "default":
                params["sub_user_id"] = user_id
            resp = await self._request("GET", "/v1/procedures", params=params)
            return resp.get("procedures", [])

    async def procedure_feedback(self, procedure_id: str, success: bool = True,
                                 context: str | None = None, failed_at_step: int | None = None,
                                 user_id: str = "default") -> dict[str, Any]:
        """Record success/failure feedback for a procedure."""
        data: dict[str, Any] | None = None
        if context is not None:
            data = {"context": context}
            if failed_at_step is not None:
                data["failed_at_step"] = failed_at_step
        params: dict[str, Any] = {"success": "true" if success else "false"}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("PATCH", f"/v1/procedures/{procedure_id}/feedback",
                                   data=data, params=params)

    # ---- Graph & Timeline ----

    async def graph(self, user_id: str = "default") -> dict[str, Any]:
        """Get knowledge graph (nodes + edges)."""
        params: dict[str, Any] = {}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("GET", "/v1/graph", params=params)

    async def timeline(self, after: str | None = None, before: str | None = None,
                       user_id: str = "default", limit: int = 20) -> list[dict[str, Any]]:
        """Temporal search — facts in a time range."""
        params: dict[str, Any] = {"limit": limit}
        if after: params["after"] = after
        if before: params["before"] = before
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        resp = await self._request("GET", "/v1/timeline", params=params)
        return resp.get("results", [])

    # ---- Agents ----

    async def run_agents(self, agent: str = "all", auto_fix: bool = False,
                         user_id: str = "default") -> dict[str, Any]:
        """Run memory agents (curator, connector, digest)."""
        params: dict[str, Any] = {"agent": agent, "auto_fix": str(auto_fix).lower()}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("POST", "/v1/agents/run", params=params)

    # ---- Insights ----

    async def reflect(self, user_id: str = "default") -> dict[str, Any]:
        """Trigger memory reflection."""
        params: dict[str, Any] = {}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("POST", "/v1/reflect", params=params)

    async def insights(self, user_id: str = "default") -> dict[str, Any]:
        """Get AI insights from memory reflections."""
        params: dict[str, Any] = {}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("GET", "/v1/insights", params=params)

    # ---- Memory Management ----

    async def dedup(self, user_id: str = "default") -> dict[str, Any]:
        """Find and merge duplicate entities."""
        params: dict[str, Any] = {}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("POST", "/v1/dedup", params=params)

    async def merge(self, source: str, target: str, user_id: str = "default") -> dict[str, Any]:
        """Merge two entities."""
        params: dict[str, Any] = {"source": source, "target": target}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("POST", "/v1/merge", params=params)

    async def archive_fact(self, entity: str, fact: str, user_id: str = "default") -> dict[str, Any]:
        """Archive a specific fact on an entity."""
        params: dict[str, Any] = {}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        return await self._request("POST", "/v1/archive_fact",
                                   {"entity_name": entity, "fact_content": fact}, params=params)

    # ---- Job Tracking ----

    async def job_status(self, job_id: str) -> dict[str, Any]:
        """Check status of a background job."""
        return await self._request("GET", f"/v1/jobs/{job_id}")

    async def wait_for_job(self, job_id: str, poll_interval: float = 1.0,
                           max_wait: float = 60.0) -> dict[str, Any]:
        """Wait for a background job to complete."""
        import asyncio
        import time
        start = time.time()
        while time.time() - start < max_wait:
            job = await self.job_status(job_id)
            if job["status"] in ("completed", "failed"):
                return job
            await asyncio.sleep(poll_interval)
        raise TimeoutError(f"Job {job_id} timed out after {max_wait}s")

    # ---- Webhooks ----

    async def create_webhook(self, url: str, name: str = "",
                             event_types: list[str] | None = None, secret: str = "") -> dict[str, Any]:
        """Create a webhook."""
        data: dict[str, Any] = {"url": url, "name": name, "secret": secret}
        if event_types: data["event_types"] = event_types
        result = await self._request("POST", "/v1/webhooks", data)
        return result.get("webhook", result)

    async def get_webhooks(self) -> list[dict[str, Any]]:
        """List all webhooks."""
        result = await self._request("GET", "/v1/webhooks")
        return result.get("webhooks", [])

    # ---- Triggers ----

    async def get_triggers(self, target_user_id: str | None = None,
                           include_fired: bool = False, limit: int = 50,
                           user_id: str = "default") -> list[dict[str, Any]]:
        """Get smart triggers."""
        params: dict[str, Any] = {"include_fired": str(include_fired).lower(), "limit": limit}
        if user_id and user_id != "default":
            params["sub_user_id"] = user_id
        path = f"/v1/triggers/{target_user_id}" if target_user_id else "/v1/triggers"
        result = await self._request("GET", path, params=params)
        return result.get("triggers", [])
