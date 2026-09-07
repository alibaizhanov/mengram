"""Public site: landing, pricing, legal pages, SEO pages (vs / blog / usecase), sitemap, llms.txt.

No auth and no store — every route here renders a template from this directory or a
dict from cloud/content. Kept out of cloud/api.py so editing a page never touches the API.
"""

import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from cloud.content.blog_posts import BLOG_POSTS
from cloud.content.usecase_pages import USECASE_PAGES
from cloud.content.vs_pages import VS_PAGES


def build_site_router(version: str) -> APIRouter:
    """All public pages. `version` is stamped into the landing and pricing templates."""
    router = APIRouter()
    __version__ = version

    # HEAD is declared alongside GET because FastAPI, unlike plain Starlette,
    # does not derive it — so link checkers answered 405 and reported the site
    # as unreachable. The Obsidian plugin review flagged authorUrl for exactly
    # this while the page served 200 to every GET.
    #
    # Every public page below does the same, for the same reason. Two GET
    # routes deliberately do not: `/unsubscribe` removes an address and
    # `/auth/github/callback` spends a one-time code, and a link checker or a
    # prefetching client hitting those would carry the side effect out.
    @router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def landing():
        """Landing page."""
        landing_path = Path(__file__).parent / "landing.html"
        html = landing_path.read_text(encoding="utf-8")
        html = html.replace("{{VERSION}}", __version__)
        return html

    @router.api_route("/pricing", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def pricing():
        """Standalone pricing page (moved off the landing 2026-07)."""
        p = Path(__file__).parent / "pricing.html"
        html = p.read_text(encoding="utf-8")
        return html.replace("{{VERSION}}", __version__)

    @router.api_route("/robots.txt", methods=["GET", "HEAD"], response_class=PlainTextResponse)
    async def robots():
        return (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /dashboard\n"
            "Disallow: /auth/\n"
            "Disallow: /v1/\n"
            "Disallow: /checkout\n"
            "Disallow: /api/playground/\n"
            "\n"
            "Sitemap: https://mengram.io/sitemap.xml"
        )

    @router.api_route("/llms.txt", methods=["GET", "HEAD"], response_class=PlainTextResponse)
    async def llms_txt():
        """llms.txt (llmstxt.org): a compact index for LLMs and agents.
        GSC 2026-09 shows agents already querying Google for our docs, and the
        prod logs show direct /llms.txt fetches that 404ed until now."""
        return (
            "# Mengram\n\n"
            "> Persistent memory for AI agents and coding tools: semantic facts, episodic events, "
            "and procedural workflows with a success/failure track record. One memory across "
            "Claude Code, Cursor, Codex, ChatGPT, and any MCP client. REST API + MCP server. "
            "Free tier; open export format (memfmt).\n\n"
            "## Docs\n\n"
            "- [Quickstart](https://docs.mengram.io/quickstart): API key, first add/search in 5 minutes\n"
            "- [MCP server](https://docs.mengram.io/mcp): remote MCP at https://mengram.io/mcp (streamable HTTP)\n"
            "- [API reference](https://docs.mengram.io/api-reference): REST endpoints, auth, quotas\n"
            "- [Agent install guide](https://mengram.io/agent-install.txt): plain-text setup instructions for agents\n\n"
            "## Key pages\n\n"
            "- [Does Claude Code remember between sessions?](https://mengram.io/blog/does-claude-code-remember-between-sessions)\n"
            "- [Long-term memory for Claude/ChatGPT via MCP](https://mengram.io/blog/give-claude-chatgpt-long-term-memory-mcp)\n"
            "- [Procedural memory in AI](https://mengram.io/blog/procedural-memory-ai-agents)\n"
            "- [Agent memory with regression tests: execution policy from outcome history](https://mengram.io/blog/agent-memory-regression-tests)\n"
            "- [Pricing](https://mengram.io/pricing): free tier, paid from $5/mo\n"
        )

    @router.api_route("/agent-install.txt", methods=["GET", "HEAD"], response_class=PlainTextResponse)
    @router.api_route("/agent-install", methods=["GET", "HEAD"], response_class=PlainTextResponse)
    async def agent_install():
        """Agent-native install guide. Plain text, structured for LLM agents
        to fetch and follow. See cloud/agent-install.txt."""
        import os as _os
        path = _os.path.join(_os.path.dirname(__file__), "agent-install.txt")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="agent-install.txt missing")

    @router.api_route("/sitemap.xml", methods=["GET", "HEAD"])
    async def sitemap():
        """XML sitemap for search engines."""
        from starlette.responses import Response
        # (url, priority, changefreq)
        pages = [
            # Core — highest priority
            ("https://mengram.io", "1.0", "weekly"),
            ("https://mengram.io/for-agents", "0.9", "weekly"),
            ("https://mengram.io/pricing", "0.9", "weekly"),
            # Claude Code — high SEO value
            ("https://mengram.io/vs/claude-mem", "0.9", "weekly"),
            # VS comparison — high SEO value
            ("https://mengram.io/vs/mem0", "0.9", "weekly"),
            ("https://mengram.io/vs/zep", "0.8", "weekly"),
            ("https://mengram.io/vs/letta", "0.8", "weekly"),
            ("https://mengram.io/vs/langmem", "0.8", "weekly"),
            ("https://mengram.io/vs/supermemory", "0.8", "weekly"),
            ("https://mengram.io/vs/cognee", "0.8", "weekly"),
            ("https://mengram.io/vs/hindsight", "0.8", "weekly"),
            # Blog — high SEO value
            ("https://mengram.io/blog", "0.8", "weekly"),
            ("https://mengram.io/blog/claude-code-compaction-context-loss", "0.9", "weekly"),
            ("https://mengram.io/blog/agent-memory-regression-tests", "0.9", "weekly"),
            ("https://mengram.io/blog/schema-lied-production-cascade", "0.8", "monthly"),
            ("https://mengram.io/blog/rrf-scores-not-similarities", "0.8", "monthly"),
            ("https://mengram.io/blog/does-claude-code-remember-between-sessions", "0.9", "weekly"),
            ("https://mengram.io/blog/claude-code-remember-project-context", "0.9", "weekly"),
            ("https://mengram.io/blog/claude-code-memory-across-machines", "0.9", "weekly"),
            ("https://mengram.io/blog/persist-context-claude-code", "0.9", "weekly"),
            ("https://mengram.io/blog/claude-code-memory-vs-memory-leak", "0.9", "weekly"),
            ("https://mengram.io/blog/claude-code-memory-md", "0.9", "weekly"),
            ("https://mengram.io/blog/memory-api-for-ai-agents", "0.9", "weekly"),
            ("https://mengram.io/blog/multi-user-memory-ai-agents", "0.9", "weekly"),
            ("https://mengram.io/blog/procedural-memory-ai-agents", "0.9", "weekly"),
            ("https://mengram.io/blog/does-cursor-remember-between-sessions", "0.9", "weekly"),
            ("https://mengram.io/blog/cursor-rules-memory", "0.9", "weekly"),
            ("https://mengram.io/blog/cursor-mcp-memory-setup", "0.9", "weekly"),
            ("https://mengram.io/blog/give-claude-chatgpt-long-term-memory-mcp", "0.9", "weekly"),
            ("https://mengram.io/blog/what-is-ai-memory", "0.8", "monthly"),
            ("https://mengram.io/blog/ai-memory-vs-rag", "0.8", "monthly"),
            ("https://mengram.io/blog/semantic-episodic-procedural-memory", "0.8", "monthly"),
            ("https://mengram.io/blog/how-to-add-memory-to-ai-agents", "0.8", "monthly"),
            ("https://mengram.io/blog/cognitive-profile-system-prompts", "0.7", "monthly"),
            ("https://mengram.io/blog/mcp-memory-server-setup", "0.8", "monthly"),
            ("https://mengram.io/blog/mem0-vs-mengram-benchmark", "0.8", "monthly"),
            ("https://mengram.io/blog/ai-memory-for-crewai-langchain", "0.7", "monthly"),
            ("https://mengram.io/blog/claude-code-memory-hooks", "0.9", "weekly"),
            ("https://mengram.io/blog/cursor-ai-memory-mcp", "0.9", "weekly"),
            ("https://mengram.io/blog/context-engineering-memory", "0.9", "weekly"),
            ("https://mengram.io/blog/claude-managed-agents-memory", "0.9", "weekly"),
            ("https://mengram.io/blog/multi-tenant-mcp-server", "0.9", "weekly"),
            ("https://mengram.io/blog/multilingual-ai-memory", "0.9", "weekly"),
            ("https://mengram.io/blog/openai-agent-builder-memory", "0.9", "weekly"),
            ("https://mengram.io/blog/ai-agent-memory-patterns", "0.9", "weekly"),
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
        today = datetime.date.today().isoformat()
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

    @router.api_route("/dashboard", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def dashboard():
        """Memory Console."""
        dashboard_path = Path(__file__).parent / "dashboard.html"
        return dashboard_path.read_text(encoding="utf-8")

    @router.api_route("/terms", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def terms():
        """Terms of Service."""
        p = Path(__file__).parent / "terms.html"
        return p.read_text(encoding="utf-8")

    @router.api_route("/privacy", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def privacy():
        """Privacy Policy."""
        p = Path(__file__).parent / "privacy.html"
        return p.read_text(encoding="utf-8")

    @router.api_route("/for-agents", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def for_agents():
        """Memory API for agent builders — segment (b) landing."""
        p = Path(__file__).parent / "for-agents.html"
        return p.read_text(encoding="utf-8")

    @router.api_route("/refund", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def refund():
        """Refund Policy."""
        p = Path(__file__).parent / "refund.html"
        return p.read_text(encoding="utf-8")

    @router.api_route("/vs/{competitor}", methods=["GET", "HEAD"], response_class=HTMLResponse)
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

    @router.api_route("/blog", methods=["GET", "HEAD"], response_class=HTMLResponse)
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

    @router.api_route("/blog/{slug}", methods=["GET", "HEAD"], response_class=HTMLResponse)
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

    @router.api_route("/usecase/{slug}", methods=["GET", "HEAD"], response_class=HTMLResponse)
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

    @router.api_route("/docs", methods=["GET", "HEAD"], response_class=RedirectResponse)
    async def docs_index():
        """Redirect to Mintlify docs."""
        return RedirectResponse("https://docs.mengram.io", status_code=301)

    @router.api_route("/docs/{slug}", methods=["GET", "HEAD"], response_class=RedirectResponse)
    async def docs_page(slug: str):
        """Redirect to Mintlify docs."""
        return RedirectResponse(f"https://docs.mengram.io/{slug}", status_code=301)

    return router
