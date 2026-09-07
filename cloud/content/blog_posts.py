"""Blog posts: /blog and /blog/{slug}.

Moved verbatim out of cloud/api.py; rendered by cloud/site.py."""

BLOG_POSTS = {
    "give-claude-chatgpt-long-term-memory-mcp": {
        "slug": "give-claude-chatgpt-long-term-memory-mcp",
        "title": "How to Give Claude or ChatGPT Long-Term Memory Using MCP",
        "date": "July 24, 2026",
        "date_iso": "2026-07-24",
        "read_time": "5",
        "tags": ["Guide", "MCP"],
        "excerpt": "The Model Context Protocol (MCP) lets you attach a persistent memory backend to Claude, ChatGPT, Cursor, and other MCP clients — so they remember across sessions. Here is exactly how the memory-over-MCP setup works and how to wire it up.",
        "seo_title": "Give Claude or ChatGPT Long-Term Memory via MCP (Copy-Paste Config)",
        "seo_description": "The exact MCP server config for Claude, ChatGPT, and Cursor, plus the one system-prompt line that makes the agent actually use its memory. 5-minute setup.",
        "seo_keywords": "give claude long-term memory mcp, chatgpt long term memory mcp, how to give claude memory, mcp memory server, long term memory claude chatgpt, claude memory mcp, add memory to claude",
        "content_html": """
<h2>The idea: memory as an MCP server</h2>
<p>The Model Context Protocol (MCP) is an open standard for connecting AI clients to external tools and data. A <strong>memory MCP server</strong> exposes tools like <code>remember</code>, <code>recall</code>, and <code>search</code> — so any MCP-capable client (Claude Desktop, Claude Code, ChatGPT with connectors, Cursor, Windsurf) can store and retrieve long-term memory that lives outside the context window and survives across sessions.</p>

<h2>Why MCP is the right layer</h2>
<p>Without it, each client is stateless — it forgets between sessions. Building memory into one client doesn't help the others. MCP solves both: memory lives in a server keyed to you, and every MCP client can reach the same store, so context built in Claude is available in Cursor and vice versa.</p>

<h2>How to set it up</h2>
<p>1. <strong>Pick a memory backend that speaks MCP.</strong> <a href="https://mengram.io">Mengram</a> runs a remote MCP server at <code>mengram.io/mcp</code> (listed in the official MCP registry); it does server-side extraction of facts, events, and workflows so you send raw text and it structures the memory.</p>
<p>2. <strong>Add it to your client's config.</strong> For Claude Desktop / Cursor, add the server to the MCP config (a JSON block with the URL and your Bearer key). For Claude Code, install the plugin. For ChatGPT, add it as a custom connector in settings.</p>
<pre><code>{
  "mcpServers": {
    "mengram": {
      "url": "https://mengram.io/mcp",
      "headers": { "Authorization": "Bearer om-your-key" }
    }
  }
}</code></pre>
<p>3. <strong>Tell the model when to use it.</strong> An MCP tool the model forgets to call is useless. Add one instruction to your system prompt / rules: "Before answering anything user-specific, call recall. When the user states a durable fact, decision, or preference, call remember."</p>

<h2>What you get</h2>
<p>Cross-session memory (it remembers you next time), cross-tool memory (same store from Claude, ChatGPT, Cursor), and — with a memory layer that does more than facts — episodic events and procedural workflows too. Claude Code users can go further with hooks that capture automatically; see <a href="/blog/does-claude-code-remember-between-sessions">does Claude Code remember between sessions</a>.</p>
<p>Try a zero-account local preview of what memory would know from your history: <code>pip install mengram-ai &amp;&amp; mengram try</code>.</p>
""",
    },
    "does-cursor-remember-between-sessions": {
        "slug": "does-cursor-remember-between-sessions",
        "title": "Does Cursor Remember Between Sessions? (And How to Make It)",
        "date": "July 24, 2026",
        "date_iso": "2026-07-24",
        "read_time": "5",
        "tags": ['Cursor', 'Guide'],
        "excerpt": "Cursor reads your .cursorrules and can reference open files, but it doesn't carry decisions, context, or history across new sessions by default. Here's what persists, what doesn't, and how to add real cross-session memory via MCP.",
        "seo_title": "Does Cursor Remember Between Sessions? What Persists and How to Add Memory (2026)",
        "seo_description": "Does Cursor remember between sessions? Not really — .cursorrules is static and context resets each session. What persists, what doesn't, and how to add persistent cross-session (and cross-tool) memory to Cursor via MCP.",
        "seo_keywords": "does cursor remember between sessions, cursor memory between sessions, cursor remember context, cursor persistent memory, cursor forgets, cursor session memory",
        "content_html": """
<h2>Short answer</h2>
<p><strong>Not by default.</strong> Cursor reads your <code>.cursorrules</code> file and can reference files you have open or @-mention, but it doesn't remember the decisions you made, the reasoning behind them, or what happened in previous sessions. Each new chat starts close to zero and you re-establish context.</p>

<h2>What persists</h2>
<ul>
<li><strong>.cursorrules / rules files</strong> — static instructions loaded into context. Good for stable conventions; limited to what you wrote by hand.</li>
<li><strong>Open / @-mentioned files</strong> — Cursor sees your code, so it can re-read it. Re-reading code isn't remembering your decisions about it.</li>
<li><strong>Codebase indexing</strong> — helps Cursor find relevant code, but it's retrieval over files, not memory of your intent or history.</li>
</ul>

<h2>What doesn't</h2>
<p>Decisions, constraints stated once, approaches you rejected, and anything from a prior chat. The rules file is a snapshot you maintain by hand, and it drifts — it'll say "3-step deploy" for a month after the process became four steps.</p>

<h2>How to add real memory to Cursor</h2>
<p>Cursor supports the Model Context Protocol (MCP), so you can give it a memory backend as an MCP server. Add a memory server to <code>~/.cursor/mcp.json</code>, then a short paragraph in your rules telling Cursor when to use it:</p>
<blockquote>Before starting significant work, call <code>recall</code> with the task topic. After completing significant work or when the user states a decision or constraint, call <code>remember</code> with a one-line summary.</blockquote>
<p>With that, capture and recall become part of every session instead of manual bookkeeping. <a href="https://mengram.io">Mengram</a> provides the MCP server (and the same memory works across Claude Code, Codex, and the API — so context built in one tool is there in the others).</p>
<p>Related: <a href="/blog/cursor-mcp-memory-setup">setting up an MCP memory server for Cursor</a> · <a href="/blog/cursor-rules-memory">.cursorrules and its limits</a></p>
""",
    },
    "cursor-rules-memory": {
        "slug": "cursor-rules-memory",
        "title": ".cursorrules and Memory: Why Rules Files Can't Remember Yesterday's Decisions",
        "date": "July 24, 2026",
        "date_iso": "2026-07-24",
        "read_time": "5",
        "tags": ['Cursor', 'Guide'],
        "excerpt": ".cursorrules gives Cursor static project instructions. It's useful for conventions but can't capture what happened in a session or the decision you made an hour ago. Here's what it's good for, where it breaks, and how to add the dynamic half.",
        "seo_title": ".cursorrules and Memory — What Rules Files Do and Their Limits (2026)",
        "seo_description": ".cursorrules gives Cursor static instructions loaded every session. What it covers, why it drifts out of date, and how to add automatic memory that captures decisions and history a rules file can't.",
        "seo_keywords": "cursor rules memory, .cursorrules, cursor rules file, make cursor remember, cursor project rules, cursor memory rules",
        "content_html": """
<h2>What .cursorrules does well</h2>
<p><code>.cursorrules</code> (and the newer rules directory) gives Cursor project-specific instructions loaded into every session: your stack, conventions, style rules, and hard constraints. It's free, version-controllable, and shared across your team. For stable facts that rarely change, it's exactly the right tool.</p>

<h2>Where it breaks</h2>
<p>A rules file is a <strong>static snapshot you maintain by hand.</strong> That creates three problems:</p>
<ul>
<li><strong>It only holds what you wrote down.</strong> The decision from an hour ago isn't in it unless you stopped and added it.</li>
<li><strong>It drifts.</strong> Your process changes; the file still describes the old one until someone updates it.</li>
<li><strong>It has no capture step.</strong> Nothing writes to it automatically — so session history, outcomes, and evolving workflows never land there.</li>
</ul>

<h2>The dynamic half: memory that updates itself</h2>
<p>Keep the boring stable stuff in <code>.cursorrules</code>, and add a memory layer for the things that change — decisions, session history, workflows. Via MCP, Cursor can call <code>remember</code>/<code>recall</code> against a backend that captures as you work and surfaces relevant context per prompt.</p>
<p><a href="https://mengram.io">Mengram</a> pairs with your rules file rather than replacing it: rules for what never changes, memory for what does. It can even generate an up-to-date rules file from what it has learned.</p>
<p>Related: <a href="/blog/does-cursor-remember-between-sessions">does Cursor remember between sessions?</a></p>
""",
    },
    "cursor-mcp-memory-setup": {
        "slug": "cursor-mcp-memory-setup",
        "title": "Setting Up an MCP Memory Server for Cursor (Step by Step)",
        "date": "July 24, 2026",
        "date_iso": "2026-07-24",
        "read_time": "5",
        "tags": ['Cursor', 'Guide'],
        "excerpt": "Cursor supports MCP, which means you can give it a persistent memory backend as a server. Here's how to wire up an MCP memory server in Cursor, plus the one rules paragraph that makes the agent actually use it.",
        "seo_title": "MCP Memory Server for Cursor — Setup Guide (2026)",
        "seo_description": "How to set up an MCP memory server for Cursor so it remembers across sessions. Configure ~/.cursor/mcp.json, add the rules paragraph that triggers recall/remember, and get cross-tool memory.",
        "seo_keywords": "cursor mcp memory server, memory mcp server cursor, cursor mcp memory, mcp memory cursor, cursor memory server setup, cursor persistent memory mcp",
        "content_html": """
<h2>Why MCP is the right path for Cursor</h2>
<p>Cursor doesn't have lifecycle hooks the way Claude Code does, but it does support the Model Context Protocol. That means you can attach a memory backend as an MCP server — the agent gets <code>remember</code> / <code>recall</code> / <code>search</code> tools, and your memory lives outside the context window and outside static files.</p>

<h2>Step 1: add the MCP server</h2>
<p>Edit <code>~/.cursor/mcp.json</code> (create it if it doesn't exist) and add your memory server. For a remote (hosted) server it looks like:</p>
<pre><code>{
  "mcpServers": {
    "mengram": {
      "url": "https://mengram.io/mcp",
      "headers": { "Authorization": "Bearer om-your-key" }
    }
  }
}</code></pre>
<p>Reload Cursor (Cmd/Ctrl+Shift+P → Reload Window). The memory tools should now be available to the agent.</p>

<h2>Step 2: the rules paragraph that makes it automatic</h2>
<p>An MCP tool the agent forgets to call is useless. Add this to your rules so recall/capture become part of every session:</p>
<blockquote>Before starting significant work, call <code>recall</code> with the task topic. After completing significant work, or when the user states a decision, preference, or constraint, call <code>remember</code> with a one-line summary.</blockquote>

<h2>Step 3 (optional): seed it and go cross-tool</h2>
<p>Because it's a memory layer, not a file, the same store works from Claude Code and the API too — context built in one tool shows up in the others. With <a href="https://mengram.io">Mengram</a> you can also preview what memory would know from your existing history with zero account: <code>pip install mengram-ai &amp;&amp; mengram try</code>.</p>
<p>Related: <a href="/blog/does-cursor-remember-between-sessions">does Cursor remember between sessions?</a> · <a href="/blog/cursor-rules-memory">.cursorrules and its limits</a></p>
""",
    },
    "memory-api-for-ai-agents": {
        "slug": "memory-api-for-ai-agents",
        "title": "How to Add a Memory API to Your AI Agent Product (Per-User Memory in ~10 Lines)",
        "date": "July 24, 2026",
        "date_iso": "2026-07-24",
        "read_time": "6",
        "tags": ['Guide', 'Agents'],
        "excerpt": "If you're building an agent product, every user needs their own memory — facts, history, and workflows that persist across sessions. Here's what a memory API needs to give you (isolation, server-side extraction, three memory types) and how to wire it up.",
        "seo_title": "Memory API for AI Agents — Add Per-User Persistent Memory to Your Agent Product (2026)",
        "seo_description": "Add a memory API to your AI agent product: per-user isolation, server-side fact/event/workflow extraction, and recall in ~10 lines. What to look for in an agent memory backend and how to integrate it via REST or MCP.",
        "seo_keywords": "memory api for ai agents, agent memory backend, ai agent memory api, multi user memory llm, per user memory, memory layer for agents, mem0 alternative api",
        "content_html": """
<h2>What "memory" means for an agent product</h2>
<p>When you ship an agent to real users, each user accumulates context — preferences, past interactions, decisions, the workflows your agent runs for them. Holding that in the context window doesn't scale (it resets, and it's not per-user). You need a <strong>memory API</strong>: a backend that stores and retrieves each user's memory, isolated from every other user's, and persists across sessions.</p>

<h2>What a good agent memory API gives you</h2>
<ul>
<li><strong>Per-user isolation.</strong> One API key, memory scoped by <code>user_id</code> — user A never sees user B's memory.</li>
<li><strong>Server-side extraction.</strong> You send raw conversation turns; the backend extracts facts, events, and workflows, deduplicates them, and resolves contradictions. You shouldn't have to prompt-engineer this yourself.</li>
<li><strong>More than facts.</strong> Semantic (facts), episodic (events/decisions), and procedural (workflows) — because agents need to remember <em>how</em> to do things, not just <em>what</em> a user said.</li>
<li><strong>Recall that ranks well.</strong> Hybrid retrieval (vector + keyword + fusion), recency/importance weighting, and honest quality signals.</li>
<li><strong>An exit path.</strong> Full export and per-user deletion — for your users' trust and your own compliance.</li>
</ul>

<h2>Wiring it up (Mengram example)</h2>
<pre><code>pip install mengram-ai

from mengram import Mengram
m = Mengram(api_key="om-...")

# each of YOUR users gets an isolated store
m.add([{"role": "user", "content": "I prefer email, and my last order arrived damaged"}],
      user_id="customer-4812")

# later, any session, any of your agents
m.search("how should I contact this customer?", user_id="customer-4812")
# -> prefers email; recent damaged-order incident</code></pre>
<p>Same isolation over MCP for tool-native agents, and webhooks if you want to react when a user's memory changes. Full API on the <a href="https://mengram.io/for-agents">agent-builder page</a>.</p>

<h2>Build vs. buy</h2>
<p>You can build memory on Postgres + pgvector yourself — many teams start there. The parts that eat time are the ones a memory API handles for you: extraction quality, contradiction resolution, decay/ranking, multi-tenant isolation done right, and the export/delete lifecycle. If memory isn't your core product, buying the layer (or self-hosting an open one) is usually the faster path.</p>
<p>Related: <a href="/blog/multi-user-memory-ai-agents">per-user memory isolation patterns</a> · <a href="/vs/mem0">Mengram vs Mem0</a></p>
""",
    },
    "multi-user-memory-ai-agents": {
        "slug": "multi-user-memory-ai-agents",
        "title": "Multi-User Memory for AI Agents: Per-User Isolation Patterns That Don't Leak",
        "date": "July 24, 2026",
        "date_iso": "2026-07-24",
        "read_time": "5",
        "tags": ['Guide', 'Agents'],
        "excerpt": "Shipping an agent to many users means each one needs isolated memory — and a leak between users is a serious bug. Here are the isolation patterns for multi-tenant agent memory, and the mistakes that cause cross-user leaks.",
        "seo_title": "Multi-User Memory for AI Agents — Per-User Isolation Patterns (2026)",
        "seo_description": "How to give each user of your AI agent isolated memory without cross-user leaks. Multi-tenant memory isolation patterns (user_id scoping, sub-users, per-key scopes) and the mistakes to avoid.",
        "seo_keywords": "multi user memory, per user memory llm, multi tenant agent memory, ai agent memory isolation, user_id memory, memory per user ai",
        "content_html": """
<h2>Why isolation is the hard part</h2>
<p>Storing memory is easy. Storing it so that user A's private facts never surface in user B's session — across thousands of users, over months — is the part that bites. A cross-user memory leak isn't a cosmetic bug; it's a privacy incident. Here's how to get it right.</p>

<h2>Pattern 1: user_id scoping (the baseline)</h2>
<p>Every write and every read carries a <code>user_id</code>. The store filters by it at query time. Simple and correct — as long as <em>every</em> path enforces it. The classic leak is a code path (an admin tool, a background job, a "get all" endpoint) that forgets the filter. Enforce it at the store layer, not per-endpoint, so nothing can bypass it.</p>

<h2>Pattern 2: sub-users (users within a user)</h2>
<p>If your product itself has tenants — say each of your customers has their own end-users — you need a second axis. A <code>sub_user_id</code> under each <code>user_id</code> gives you two levels of isolation without two accounts. Useful for B2B2C agent products.</p>

<h2>Pattern 3: per-key scopes</h2>
<p>Hand a component an API key that can only write to a specific scope. The agent physically cannot pollute memory outside its lane — isolation enforced by the credential, not by discipline.</p>

<h2>The mistakes that cause leaks</h2>
<ul>
<li><strong>Filtering at read but not at write</strong> — mislabeled writes end up in the wrong bucket permanently.</li>
<li><strong>Enforcing per-endpoint</strong> instead of at the store layer — one forgotten filter leaks.</li>
<li><strong>Sharing embeddings across users</strong> — vector search returns another user's vectors if the namespace isn't scoped.</li>
<li><strong>No capture boundary</strong> — sensitive content (health, legal, credentials) gets stored when it shouldn't, per user, with no way to scope it out.</li>
</ul>

<h2>Doing it with a memory layer</h2>
<p><a href="https://mengram.io/for-agents">Mengram</a> gives you <code>user_id</code> isolation and a <code>sub_user_id</code> axis out of the box (one API key, isolated facts/events/workflows/profile per user), plus a server-side capture policy so sensitive categories are dropped before they're ever stored. Deletion is per-user and complete, with a per-table receipt.</p>
<p>Related: <a href="/blog/memory-api-for-ai-agents">adding a memory API to your agent product</a></p>
""",
    },
    "procedural-memory-ai-agents": {
        "slug": "procedural-memory-ai-agents",
        "title": "Procedural Memory for AI Agents: Workflows That Learn From Failure",
        "date": "July 24, 2026",
        "date_iso": "2026-07-24",
        "read_time": "6",
        "tags": ['Guide', 'Agents'],
        "excerpt": "Most agent memory stores facts. Procedural memory stores how-to — the workflows an agent repeats — and crucially, revises them when they fail. Here's what procedural memory is, why it's the underserved layer, and how to use it.",
        "seo_title": "Procedural Memory in AI: How Agents Learn Workflows From Failure",
        "seo_description": "What procedural memory is in AI, why fact-only memory tools skip it, and how agents store versioned workflows that revise themselves when a run fails.",
        "seo_keywords": "procedural memory ai agents, procedural memory llm, agent workflow memory, agent learns from failure, memp procedural memory, ai agent skill memory",
        "content_html": """
<h2>Three kinds of memory, and the one everyone skips</h2>
<p>Psychology splits long-term memory into <strong>semantic</strong> (facts — "Paris is the capital of France"), <strong>episodic</strong> (events — "I visited Paris last spring"), and <strong>procedural</strong> (how-to — "I know how to ride a bike"). Nearly every AI memory tool ships the first two and skips the third. But procedural memory is where agents waste the most: an agent that re-derives your deploy process from scratch every run is a permanent intern, however smart the model.</p>

<h2>Why procedural memory is harder</h2>
<p>A fact is extracted once and stored. A workflow isn't — it has to <em>change</em> when it fails. A deploy procedure that worked ten times can break on the eleventh because an assumption shifted ("the migration had already run"). Procedural memory has to capture that failure and revise the workflow, or it rots into the same stale instructions it was meant to replace.</p>
<p>There's fresh research on exactly this — the Memp paper (Zhejiang University + Alibaba) built procedural memory from agents' own trajectories and found the strongest strategy was <em>reflecting on failures to revise the stored procedure</em>, not just banking successes.</p>

<h2>What good procedural memory records</h2>
<ul>
<li><strong>Versioned steps</strong> — v1 → v2 (added a step after a failure) → v3, not overwrite-in-place.</li>
<li><strong>The violated assumption</strong> — not "step 3 failed" but "the belief that the migration had run turned out false." That's what prevents the repeat.</li>
<li><strong>A precondition to check next time</strong> — derived from the failure, carried into recall so the agent verifies before trusting the workflow.</li>
<li><strong>Success/failure counts per version</strong> — so a revision has to re-earn trust instead of inheriting it.</li>
</ul>

<h2>Using it</h2>
<p><a href="https://mengram.io">Mengram</a> stores procedural memory as a first-class type: workflows auto-detected from repeated episodes, evolved on failure (recording the violated assumption + precondition), and returned by recall alongside their track record. An agent loading a proven v3 doesn't repeat the two mistakes that produced it.</p>
<pre><code>m.procedures(query="deploy backend", user_id="user-123")
# -> deploy-to-railway (v3, 11 successes) — verify first: alembic current == head</code></pre>
<p>Related: <a href="/blog/semantic-episodic-procedural-memory">the three memory types explained</a> · <a href="/for-agents">memory API for agent builders</a></p>
""",
    },
    "agent-memory-regression-tests": {
        "slug": "agent-memory-regression-tests",
        "title": "Agent Memory With Regression Tests: An Execution Policy From Outcome History",
        "date": "September 4, 2026",
        "date_iso": "2026-09-04",
        "read_time": "7",
        "tags": ['Guide', 'Agents'],
        "excerpt": "Every agent-memory tool stores the steps of a workflow. Almost none store whether the steps worked — and none let that record change what the agent is allowed to run. Here is what it takes: a trust label per version, a policy gate that asks before a weak workflow runs, and a regression gate that catches one revision silently breaking another.",
        "seo_title": "Agent Memory With Regression Tests — Execution Policy From Outcome History",
        "seo_description": "How to turn an agent's workflow memory into an execution policy: success/failure counts per version, a Claude Code hook that asks before running an untested workflow, and a regression gate with a public benchmark (0% silent regressions vs 100% for latest-wins).",
        "seo_keywords": "agent memory regression tests, execution policy ai agent, procedural memory outcome tracking, agent workflow reliability, claude code pretooluse hook, memfmt, cross-procedure interference",
        "content_html": """
<h2>The steps are stored. Whether they worked is not.</h2>
<p>Look inside any agent-memory tool and you will find the workflow the agent learned: push to main, watch the boot log, verify <code>/health</code>. What you will almost never find is whether that workflow has ever succeeded, how many times, and what broke the last time it did not. The steps are a description. Without an outcome next to them they are a guess somebody wrote down, and an agent reading a guess with confidence is worse than an agent with no memory at all.</p>
<p>This post is about the three pieces that turn a stored workflow into evidence an agent can act on: a <strong>trust label</strong> per version, a <strong>policy gate</strong> that changes what the agent may run, and a <strong>regression gate</strong> that catches one revision silently breaking another. All three ship in <a href="https://mengram.io">Mengram</a> today; the file format underneath is <a href="https://github.com/alibaizhanov/memfmt">open</a>.</p>

<h2>1. A trust label, not a ratio</h2>
<p>Every version of a procedure carries <code>success_count</code> and <code>fail_count</code>. The obvious thing to show an agent is the ratio, and the obvious thing is wrong twice. One success reads as 100%. And a fresh revision, written precisely because the previous version failed, opens at 0/0 and reads <em>worse</em> than the version it replaced — so an agent comparing the two keeps choosing the one that already broke.</p>
<p>Progressive delivery and CI hit this years ago and answered it the same way: smooth against a prior instead of comparing raw counts. So the label an agent reads is one of three words:</p>
<ul>
<li><code>untested</code> — no runs, no lineage. Nothing to go on, and inventing a number would be worse than saying so.</li>
<li><code>61% expected</code> — no runs of its own; this is what the previous version's record suggests, discounted by half.</li>
<li><code>86% reliable</code> — its own record, smoothed so <code>11✓/1✗</code> does not read as 92%.</li>
</ul>
<p>The raw counts stay in the record unsmoothed. The word is derived, the record is data. Since memfmt 0.5 the record also carries <code>last_failure</code>: one line of <em>why</em> it last went wrong. The counts say how often; that line says what to look at first.</p>

<h2>2. Outcome history should change what the agent may do</h2>
<p>Ranking a weak workflow lower in retrieval is not the same as stopping the agent from running it. A comment in a thread on this put it exactly: a workflow with weak or stale evidence should drop from auto-run to "show me the plan first", not merely rank lower.</p>
<p>So the label is wired into a <a href="https://docs.mengram.io/claude-code">Claude Code <code>PreToolUse</code> hook</a>. When Claude is about to run a workflow-shaped command — <code>git push</code>, <code>deploy</code>, <code>migrate</code>, <code>kubectl</code>, <code>rm -rf</code> — the hook finds the learned procedure it matches and reads the record:</p>
<pre><code>88% reliable   → silent; the command runs as it would have
untested       → ask: you see why, Claude gets the steps on record
61% expected   → ask: this version has never run on its own
58% reliable   → ask: below the bar (default 70%)</code></pre>
<p>Two design rules. The gate <strong>never denies</strong>: memory can ask, it does not get to forbid, because the same 61% should mean "plan first" in a deploy harness and "just run it" in a scratch notebook, and only the runtime knows which one it is in. And a matched procedure must share at least one real word with the command before it can interrupt anyone — semantic search returns <em>something</em> for almost any query, and a vector near-miss must not cost a human a click.</p>
<pre><code>pip install -U mengram-ai &amp;&amp; mengram hook install     # the gate is hook #4
MENGRAM_POLICY_MIN_RELIABLE=80                        # raise the bar
MENGRAM_MEMORY_DIR=./memory                           # offline, against a memfmt folder</code></pre>

<h2>3. Regression tests for memory</h2>
<p>Revising a workflow is where memory systems quietly corrupt themselves. Revising procedure A to add "run migrations before push" is correct for A — and silently invalidates procedure B, which shares the database and never runs migrations. Every procedural-memory paper of 2025–26 evaluates learned skills in isolation; the AFTER authors list cross-skill interference as an open problem. Nobody was measuring it, so we wrote the benchmark.</p>
<p><a href="https://github.com/alibaizhanov/mengram/tree/main/benchmark/procinterfere">ProcInterfere</a>: 20 paired cases across 12 domains (Postgres, S3, Stripe, GitHub, Terraform, Redis, Kafka, SQS, Cloudflare, OpenAI, LaunchDarkly, Railway). The metric is the silent-regression rate — the share of revisions that break a dependent procedure and get promoted anyway.</p>
<pre><code>system          silent-regression   false-quarantine
latest-wins                 100%                0%
append-only                 100%                0%
mengram-gate                  0%                0%</code></pre>
<p>The gate is deterministic code, no model call on the hot path: before a revision becomes current it checks whether the revision adds a constraint a dependent procedure does not satisfy, and quarantines it as <code>needs_review</code> instead of shipping it to the agent. <code>python run.py</code>, no account, no key.</p>

<h2>What this does not do yet</h2>
<p>Staleness. A workflow that worked reliably three months ago can start failing once the API under it changes, and "unverified in 90 days" should be its own signal. The honest blocker: the timestamp most memory systems keep is touched by retrieval, not only by runs, so deriving "last verified" from it would be a lie. It needs its own field, written only when an outcome is recorded — <code>last_failed</code> exists as of memfmt 0.5; <code>last_succeeded</code> is next.</p>
<p>And prior art is real. Failure-driven revision of stored procedures is in Memp, MACLA and PRAXIS; the smoothed trust label is a canary confidence record with a different name. What was missing was the last mile: the label changing what the agent is allowed to do, and a test that catches a revision breaking its neighbours.</p>
<p>Related: <a href="/blog/procedural-memory-ai-agents">procedural memory for AI agents</a> · <a href="https://docs.mengram.io/claude-code">Claude Code integration</a> · <a href="https://github.com/alibaizhanov/memfmt">memfmt, the open format</a></p>
""",
    },
    "persist-context-claude-code": {
        "slug": "persist-context-claude-code",
        "title": "How to Persist Context in Claude Code (So It Doesn't Start From Zero)",
        "date": "July 23, 2026",
        "date_iso": "2026-07-23",
        "read_time": "5",
        "tags": ['Claude Code', 'Guide'],
        "excerpt": "Persisting context in Claude Code means keeping the things that matter outside the context window and reloading them automatically. Here's the mechanism — the SessionStart and Stop hooks — and how to wire it up, with or without a memory service.",
        "seo_title": "Persist Context in Claude Code: Copy-Paste Hooks Setup (2026)",
        "seo_description": "Stop starting from zero: capture context with a Stop hook and reload it on SessionStart — copy-paste setup included, plus where CLAUDE.md falls short.",
        "seo_keywords": "persist context claude code, claude code persist context, keep context claude code, claude code context persistence, claude code save context, claude code load context",
        "content_html": """
<h2>What "persist context" actually requires</h2>
<p>To persist context in Claude Code, you need two things: a place to <em>store</em> context that survives the session (outside the context window), and a moment to <em>reload</em> it into a fresh session. Claude Code gives you both as lifecycle hooks — the missing piece most setups skip is the storage layer.</p>

<h2>The hook mechanism</h2>
<ul>
<li><strong>Stop hook</strong> — fires when Claude finishes a turn. This is where you capture what happened (the decision, the outcome, the workflow) into durable storage.</li>
<li><strong>SessionStart hook</strong> — fires on a new session, on <code>/clear</code>, on resume, and <em>after compaction</em>. This is where you read state back and print it so Claude sees it as context.</li>
<li><strong>UserPromptSubmit hook</strong> — fires before each prompt; optionally fetch only the context relevant to that prompt instead of front-loading everything.</li>
</ul>
<p>The pattern works with any storage — a JSON file, a database, or a memory service. The hooks are the seam; the storage is your choice.</p>

<h2>Why CLAUDE.md is only half of it</h2>
<p>CLAUDE.md is a static reload with no capture step — it holds what you wrote by hand, not what happened. Persisting context properly means the capture half runs automatically, so you're not relying on remembering to update a file.</p>

<h2>Doing it with a memory layer</h2>
<p><a href="https://mengram.io">Mengram</a> implements this loop as a plugin: the Stop hook extracts facts, decisions, and workflows into persistent memory (secrets redacted locally), and the SessionStart hook reloads a distilled profile every session. Two commands:</p>
<pre><code>mkdir -p ~/.mengram && echo '{"api_key": "om-your-key"}' > ~/.mengram/config.json
claude plugin marketplace add alibaizhanov/mengram
claude plugin install mengram@mengram</code></pre>
<p>Prefer to roll your own? The same hook events are documented by Anthropic — a small script writing to a local file gets you a working prototype. The point is to have <em>both</em> halves: capture on Stop, reload on SessionStart.</p>
<p>Related: <a href="/blog/does-claude-code-remember-between-sessions">does Claude Code remember between sessions?</a> · <a href="/blog/claude-code-compaction-context-loss">surviving auto-compaction</a></p>
""",
    },
    "claude-code-memory-vs-memory-leak": {
        "slug": "claude-code-memory-vs-memory-leak",
        "title": "Claude Code Memory — Two Very Different Problems (Persistent Memory vs. RAM Leaks)",
        "date": "July 23, 2026",
        "date_iso": "2026-07-23",
        "read_time": "4",
        "tags": ['Claude Code', 'Guide'],
        "excerpt": "Searching 'Claude Code memory' returns two unrelated things: developers wanting persistent memory across sessions, and developers fighting a RAM memory leak. Here's how to tell which one you have and what to do about each.",
        "seo_title": "Claude Code Memory: Persistent Context vs. RAM Memory Leak (Which Do You Have?)",
        "seo_description": "'Claude Code memory' means two different things: persistent memory across sessions, or a RAM/OOM memory leak. How to tell them apart and fix each — the persistent-memory setup and the memory-leak workarounds.",
        "seo_keywords": "claude code memory, claude code memory leak, claude code out of memory, claude code RAM, claude code persistent memory, claude code memory usage",
        "content_html": """
<h2>Two problems, one search term</h2>
<p>"Claude Code memory" is ambiguous. Developers searching it want one of two completely different things:</p>
<ol>
<li><strong>Persistent memory</strong> — "why does Claude Code forget my project every session?"</li>
<li><strong>A RAM memory leak</strong> — "why is Claude Code using 120 GB of RAM and getting OOM-killed?"</li>
</ol>
<p>This page disambiguates so you land on the right fix.</p>

<h2>If you have the RAM leak</h2>
<p>There are real, heavily-upvoted reports of Claude Code memory <em>consumption</em> growing until the process is OOM-killed (<a href="https://github.com/anthropics/claude-code/issues/4953">issue #4953</a>, 73+ upvotes; <a href="https://github.com/anthropics/claude-code/issues/11315">#11315</a>, 56+). Practical mitigations while Anthropic addresses it: restart long-running idle sessions, keep an eye on the <code>/tmp/claude-*</code> working files (<a href="https://github.com/anthropics/claude-code/issues/8856">#8856</a>), and avoid extremely long single sessions. This is a runtime bug, not something a memory tool fixes — track the issues above.</p>

<h2>If you want persistent memory</h2>
<p>If your actual problem is that Claude Code forgets your context between sessions, that's a different thing entirely — and it <em>is</em> solvable. Claude Code doesn't carry decisions, constraints, or working state across a new session or an auto-compaction by default. The fix is a memory layer wired to the SessionStart/Stop hooks that captures context as you work and reloads it every session.</p>
<p><a href="https://mengram.io">Mengram</a> does this via its plugin; see <a href="/blog/does-claude-code-remember-between-sessions">does Claude Code remember between sessions</a> and <a href="/blog/claude-code-remember-project-context">how to make Claude Code remember your project</a> for the full walkthrough.</p>

<h2>Quick test: which one is it?</h2>
<p>Open Activity Monitor / Task Manager while Claude Code runs. If RAM climbs without bound → you have the leak (a runtime issue). If RAM is fine but Claude keeps forgetting what you told it → you want persistent memory (a solvable setup).</p>
""",
    },
    "claude-code-memory-md": {
        "slug": "claude-code-memory-md",
        "title": "Claude Code memory.md and CLAUDE.md: What They Do and Where They Fall Short",
        "date": "July 23, 2026",
        "date_iso": "2026-07-23",
        "read_time": "5",
        "tags": ['Claude Code', 'Guide'],
        "excerpt": "CLAUDE.md (and memory-style markdown files) give Claude Code static project instructions. They're useful and free — but they're snapshots you maintain by hand. Here's exactly what they cover, what they don't, and how to add the dynamic half.",
        "seo_title": "Claude Code memory.md / CLAUDE.md — What It Does and Its Limits (2026)",
        "seo_description": "CLAUDE.md and memory markdown files give Claude Code static instructions loaded every session. What they cover, why they go stale, and how to add automatic, dynamic memory that captures decisions as they happen.",
        "seo_keywords": "claude code memory.md, claude.md, claude code memory files, claude code memory file, claude md file, claude code instructions file",
        "content_html": """
<h2>What CLAUDE.md does</h2>
<p><code>CLAUDE.md</code> is a markdown file at your repo root that Claude Code loads into context at the start of every session. It's the standard way to give Claude persistent, project-specific instructions: your stack, coding conventions, directory layout, and hard rules ("always run migrations before deploy"). It's free, simple, and version-controllable — commit it and your whole team shares the same baseline.</p>

<h2>Where it falls short</h2>
<p>CLAUDE.md is a <strong>static snapshot you maintain by hand.</strong> Three concrete limits:</p>
<ul>
<li><strong>It only holds what you remembered to write.</strong> The decision you made forty minutes ago isn't in it unless you stopped and added it — and nobody does that reliably.</li>
<li><strong>It goes stale.</strong> Your deploy process changes from 3 steps to 4; the file still says 3 until someone updates it.</li>
<li><strong>It fades after compaction.</strong> There's a known issue where CLAUDE.md guidance loses force once a session heavily compacts (<a href="https://github.com/anthropics/claude-code/issues/6354">#6354</a>).</li>
</ul>

<h2>The dynamic half: memory that updates itself</h2>
<p>CLAUDE.md is the right tool for stable facts. For the things that change — decisions, session history, evolving workflows — you want capture that runs automatically. Claude Code's Stop and SessionStart hooks make this possible: capture each turn's important state, reload it every new session.</p>
<p><a href="https://mengram.io">Mengram</a> pairs with CLAUDE.md rather than replacing it: keep the boring stable facts in the file, let the plugin handle the dynamic memory. It can even generate an up-to-date CLAUDE.md from what it has learned (<code>mengram rules</code>). Setup:</p>
<pre><code>mkdir -p ~/.mengram && echo '{"api_key": "om-your-key"}' > ~/.mengram/config.json
claude plugin marketplace add alibaizhanov/mengram
claude plugin install mengram@mengram</code></pre>
<p>Related: <a href="/blog/claude-code-remember-project-context">4 methods to make Claude Code remember your project</a>.</p>
""",
    },
    "does-claude-code-remember-between-sessions": {
        "slug": "does-claude-code-remember-between-sessions",
        "title": "Does Claude Code Remember Between Sessions? (What It Keeps, What It Forgets)",
        "date": "July 23, 2026",
        "date_iso": "2026-07-23",
        "read_time": "6",
        "tags": ['Claude Code', 'Guide'],
        "excerpt": "Short answer: partially. Claude Code can resume a session and read CLAUDE.md, but it does not carry your decisions, context, or working state across new sessions by default. Here's exactly what persists, what doesn't, and how to get true cross-session memory.",
        "seo_title": "Does Claude Code Remember Between Sessions? The Fix (2026)",
        "seo_description": "Partially. Decisions, context, and working state vanish on new sessions and compaction. See exactly what persists — and add true cross-session memory in 2 commands.",
        "seo_keywords": "does claude code remember between sessions, claude code memory between sessions, claude code remember context, claude code persistent memory, claude code session memory, claude code forgets",
        "content_html": """
<h2>The short answer</h2>
<p><strong>Partially.</strong> Claude Code can <em>resume</em> a previous session and it reads your <code>CLAUDE.md</code> on start — but by default it does <strong>not</strong> carry your decisions, the reasoning behind them, or your working context across a genuinely new session, a <code>/clear</code>, or an auto-compaction. Each fresh session starts close to zero and you re-explain.</p>

<h2>What DOES persist</h2>
<ul>
<li><strong><code>CLAUDE.md</code> / <code>AGENTS.md</code>:</strong> static instructions you wrote by hand. Loaded every session — but only holds what you remembered to write down, and even this loses force after heavy compaction (<a href="https://github.com/anthropics/claude-code/issues/6354">issue #6354</a>).</li>
<li><strong><code>--resume</code> / <code>--continue</code>:</strong> re-opens a specific prior conversation. Useful, but it's one thread — it doesn't give you cumulative memory across all your work, and a resumed session still compacts.</li>
<li><strong>Project files:</strong> your code is on disk, so Claude can re-read it. But re-reading a codebase is not the same as remembering the <em>decisions</em> you made about it.</li>
</ul>

<h2>What does NOT persist</h2>
<ul>
<li>Decisions and the reasoning behind them ("we chose Postgres over Mongo because…")</li>
<li>Constraints you stated once ("never touch the billing table directly")</li>
<li>Approaches you already tried and rejected</li>
<li>Working state mid-task after auto-compaction summarizes the conversation and discards the original — a pain with <a href="https://github.com/anthropics/claude-code/issues/17428">300+ combined upvotes</a> on Anthropic's tracker</li>
</ul>

<h2>Why CLAUDE.md isn't enough</h2>
<p>The usual advice — "put it in CLAUDE.md" — helps but has a ceiling: the file is static. It captures last week's snapshot, not the decision from forty minutes ago. And nothing auto-updates it: you have to notice something is worth remembering, stop, and write it down. In practice nobody does that reliably, so the file drifts out of date.</p>

<h2>How to get true cross-session memory</h2>
<p>The durable fix is to keep memory <strong>outside</strong> the context window and re-inject it on every fresh start. Claude Code's <code>SessionStart</code> hook fires on startup, on <code>/clear</code>, on resume, <em>and after compaction</em> — the exact seam where you can reload state that the session lost.</p>
<p><a href="https://mengram.io">Mengram</a> uses this: a Stop hook captures each turn into persistent memory (secrets redacted locally), and the SessionStart hook reloads your cognitive profile — who you are, what you're building, what you decided — every new session. Setup is two commands:</p>
<pre><code>mkdir -p ~/.mengram && echo '{"api_key": "om-your-key"}' > ~/.mengram/config.json
claude plugin marketplace add alibaizhanov/mengram
claude plugin install mengram@mengram</code></pre>
<p>You can also preview what memory would know from your existing history with zero account: <code>pip install mengram-ai && mengram import claude-code</code>.</p>

<h2>Honest limits</h2>
<p>No external memory restores the full pre-compaction transcript — that's gone. What changes is <em>which</em> things survive: structured facts, decisions, and workflows extracted while they were fresh, instead of whatever a token-pressured summary happened to keep. For most "why does Claude keep forgetting my project" frustration, that's the difference that matters.</p>
<p>Related reading: <a href="/blog/claude-code-compaction-context-loss">why compaction erases context and how to survive it</a> · <a href="/blog/persist-context-claude-code">persist context with hooks (copy-paste setup)</a> · <a href="/blog/claude-code-remember-project-context">4 ways to make Claude Code remember your project</a>. Or <a href="/#signup">get an API key</a> and set it up in two commands.</p>
""",
    },
    "claude-code-remember-project-context": {
        "slug": "claude-code-remember-project-context",
        "title": "How to Make Claude Code Remember Your Project (Stop Re-Explaining Every Session)",
        "date": "July 23, 2026",
        "date_iso": "2026-07-23",
        "read_time": "6",
        "tags": ['Claude Code', 'Guide'],
        "excerpt": "Re-explaining your stack, conventions, and decisions at the start of every Claude Code session is the #1 friction developers report. Here are the four ways to make project context stick — from CLAUDE.md to hooks-based persistent memory — with the trade-offs of each.",
        "seo_title": "How to Make Claude Code Remember Your Project (4 Methods)",
        "seo_description": "Four ways to stop re-explaining your project every session — CLAUDE.md, rules files, resume, and hook-based memory — with honest trade-offs and setup for each.",
        "seo_keywords": "claude code remember project, claude code project context, claude code forgets project, make claude code remember, claude code context between sessions, claude code memory project",
        "content_html": """
<h2>The friction</h2>
<p>Every new Claude Code session, the same ritual: re-explain the stack, re-state the conventions, and watch it suggest the approach you rejected two weeks ago. The concrete cost is real time — re-establishing context can eat the first 15-30 minutes of a session. Here are the four ways to make project context stick, weakest to strongest.</p>

<h2>1. CLAUDE.md (static, manual)</h2>
<p>A <code>CLAUDE.md</code> at your repo root is loaded into every session. Put your stack, conventions, and hard constraints there. <strong>Good for:</strong> stable facts that rarely change (language, framework, "always use pnpm"). <strong>Weakness:</strong> it's static and manual — it holds what you remembered to write down, not what happened in yesterday's session, and it goes stale unless you maintain it. After heavy compaction even its guidance fades.</p>

<h2>2. Rules files (scoped, still static)</h2>
<p>Break guidance into focused rule files. More organized than one big CLAUDE.md, same fundamental limit: static snapshots that depend on you updating them.</p>

<h2>3. --resume / --continue (one thread)</h2>
<p>Re-open a specific past conversation to carry its context forward. <strong>Good for:</strong> picking up exactly where you left off on one task. <strong>Weakness:</strong> it's a single thread, not cumulative project memory, and a resumed session still compacts and loses state.</p>

<h2>4. Hooks-based persistent memory (dynamic, automatic)</h2>
<p>The only approach that captures decisions <em>as they happen</em> and reloads them automatically. Claude Code's <code>Stop</code> hook can persist each turn to an external store; the <code>SessionStart</code> hook reloads a distilled profile of your project every new session — including after <code>/clear</code> and compaction, where the other methods lose ground.</p>
<p>This is what <a href="https://mengram.io">Mengram</a>'s plugin does. Beyond facts, it also learns <em>procedural</em> memory — the workflows you repeat (deploy, test, release) — and when one fails, it records the assumption that broke so the next run doesn't repeat the mistake. Setup:</p>
<pre><code>mkdir -p ~/.mengram && echo '{"api_key": "om-your-key"}' > ~/.mengram/config.json
claude plugin marketplace add alibaizhanov/mengram
claude plugin install mengram@mengram
# optional: seed it from your existing history (secrets redacted locally)
pip install mengram-ai && mengram import claude-code</code></pre>

<h2>Which should you use?</h2>
<p>Use <strong>CLAUDE.md</strong> for boring stable facts (it's free and simple), and add <strong>hooks-based memory</strong> for the dynamic stuff — decisions, session history, and workflows that a static file can't keep up with. They compose: the file for what never changes, memory for what does.</p>
<p>Related: <a href="/blog/does-claude-code-remember-between-sessions">does Claude Code remember between sessions?</a> and <a href="/blog/claude-code-compaction-context-loss">surviving auto-compaction</a>.</p>
""",
    },
    "claude-code-memory-across-machines": {
        "slug": "claude-code-memory-across-machines",
        "title": "Claude Code Memory Across Machines: Portable Project Context for Multi-Device Work",
        "date": "July 23, 2026",
        "date_iso": "2026-07-23",
        "read_time": "5",
        "tags": ['Claude Code', 'Guide'],
        "excerpt": "Work on Claude Code from a laptop and a desktop — or switch between Claude Code and Cursor — and your context doesn't follow you. There's a 34-upvote feature request for portable project memory. Here's how to get it today.",
        "seo_title": "Claude Code Memory Across Machines — Portable Context for Multi-Device Dev (2026)",
        "seo_description": "Claude Code memory doesn't follow you between machines or tools. A 34-upvote feature request asks for portable project memory. How to get cross-machine, cross-tool memory today with a hosted or self-hosted memory layer.",
        "seo_keywords": "claude code memory across machines, claude code multi device, portable claude code memory, claude code memory sync, cross machine claude code, claude code cursor shared memory",
        "content_html": """
<h2>The problem</h2>
<p>Your <code>CLAUDE.md</code> lives in one repo on one machine. Switch to your other laptop, or move from Claude Code to Cursor, and the context you built doesn't come with you. There's an open feature request on Anthropic's tracker for <a href="https://github.com/anthropics/claude-code/issues/25739">portable project memory across machines</a> (34+ upvotes) — it's a recognized gap.</p>

<h2>Why local files don't solve it</h2>
<p>CLAUDE.md and rules files are per-repo, per-machine. You can commit them to git to sync across machines, but that only covers static instructions — not session history, decisions, or the working memory that accumulates as you use the tool. And it does nothing for cross-<em>tool</em> portability (Claude Code ↔ Cursor ↔ Codex).</p>

<h2>The fix: memory in a layer, not a file</h2>
<p>If memory lives in a hosted (or self-hosted) layer keyed to <em>you</em> rather than to a file on one disk, it follows you everywhere that can reach it. <a href="https://mengram.io">Mengram</a> works this way: the same memory is available from Claude Code on your laptop, Claude Code on your work machine, Cursor via MCP, and the API — because it's one store, not a file.</p>
<pre><code># same two commands on every machine — same memory
mkdir -p ~/.mengram && echo '{"api_key": "om-your-key"}' > ~/.mengram/config.json
claude plugin marketplace add alibaizhanov/mengram
claude plugin install mengram@mengram</code></pre>
<p>For Cursor or other MCP-capable tools, point them at the same account over MCP and the context built in one tool is there in the other.</p>

<h2>Privacy and self-hosting</h2>
<p>If a hosted store isn't acceptable for your work, the core is Apache 2.0 and self-hostable — run it on your own infra and keep the same portable-memory behavior across your machines. You can also scope what gets captured (deny by category or keyword) so sensitive content never leaves your machine in the first place.</p>
<p>Related: <a href="/blog/does-claude-code-remember-between-sessions">does Claude Code remember between sessions?</a></p>
""",
    },
    "rrf-scores-not-similarities": {
        "slug": "rrf-scores-not-similarities",
        "title": "Our Monitoring Said 62% of Retrievals Were Failing. The Bug Was Two Score Scales in One Column.",
        "date": "July 23, 2026",
        "date_iso": "2026-07-23",
        "read_time": "5",
        "tags": ["Engineering", "RAG"],
        "excerpt": "A near-miss production incident: RRF fusion scores (~1/60) and cosine rerank scores (0-1) logged into the same top_score column made healthy retrieval look catastrophic. Why a fused ranking score is not a similarity, and how to monitor hybrid search without 3am false alarms.",
        "seo_title": "RRF Scores Are Not Similarities: A Hybrid-Search Monitoring Post-Mortem",
        "seo_description": "Reciprocal Rank Fusion outputs ~1/60 for a rank-1 hit; cosine rerank outputs 0-1. Mixing both in one score column made 62% of retrievals look failed. How to monitor hybrid search correctly — count zeros, not thresholds.",
        "seo_keywords": "reciprocal rank fusion score, RRF score meaning, hybrid search monitoring, rerank vs fusion score, RAG retrieval quality, rrf k=60, vector search score threshold",
        "content_html": """
<h2>The scare</h2>
<p>Hybrid retrieval over personal memory — vector similarity + BM25, fused with Reciprocal Rank Fusion, optional cross-encoder rerank on some tiers. Every search logs <code>top_score</code> for quality monitoring. Analyzing 10,706 logged searches, I applied the obvious threshold — <code>top_score &lt; 0.3</code> = weak retrieval. Result: 62% "failures," a dozen users at "100% failure with avg score 0.017," and a terrifying month-over-month "degradation." One of the "100% failed" users was a paying customer with a thousand searches. I was halfway into incident mode.</p>

<h2>The tell</h2>
<p>A search for an exact entity name — a guaranteed hit — logged top_score 0.0426. And the "failing" users all averaged 0.016-0.021. Then it clicked: RRF scores are <code>1/(k + rank)</code> with the standard k=60. Top rank = 1/60 ≈ 0.0167. My "catastrophic" users weren't failing — <strong>their top result was rank-1 almost every time.</strong> An average of 0.017 is what <em>perfect</em> RRF retrieval looks like.</p>

<h2>What actually happened</h2>
<p>Requests that go through the reranker log cosine-style scores (0-1 scale, 0.3+ = good). Requests on the raw RRF path log fusion scores (0.016-0.05 scale, where 0.017 = excellent). Both landed in the same <code>top_score</code> column with no scale tag. Every aggregate over that column — means, z-scores, my failure thresholds, even the health-monitoring cron — was averaging apples with orbital velocities. The "month-over-month degradation" was just the RRF-path share growing as more traffic moved to hybrid.</p>
<p>What survived scale-correction: true failure (zero results) was 9-13%, driven mostly by two accounts whose agents were querying literally empty stores — a real problem, but a completely different one than "retrieval is broken."</p>

<h2>Lessons that generalize</h2>
<ol>
<li><strong>A fused ranking score is not a similarity.</strong> RRF outputs rank information, not confidence. The moment you fuse, the score's absolute value stops meaning what your dashboards think it means.</li>
<li><strong>Never store scores from different scoring regimes in one unlabeled column.</strong> Log a <code>score_kind</code> (or a scale-aware quality label computed at write time) — analysis-time guessing is how you get 3am false incidents.</li>
<li><strong>The only scale-free failure signal is emptiness.</strong> Zero results means the same thing on every path. When in doubt, count zeros, not thresholds.</li>
<li><strong>Validate your alarm against a known-good query before believing it.</strong> One exact-match search that "scored 0.04" saved me from paging myself.</li>
</ol>
<p>The k=60 default everyone inherits comes from Cormack, Clarke &amp; Buettcher (2009), "Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods." The trap applies to any RAG stack mixing rerankers with fusion scoring — grep your score column and look for a bimodal cluster around 1/60.</p>
<p><em>Context: this is Mengram (an AI memory layer); the fix — a scale-aware quality label written alongside every search — is in the public commit history.</em></p>
""",
    },
    "schema-lied-production-cascade": {
        "slug": "schema-lied-production-cascade",
        "title": "Our Schema Declared ON DELETE CASCADE. Production Didn't Have It.",
        "date": "July 23, 2026",
        "date_iso": "2026-07-23",
        "read_time": "5",
        "tags": ["Engineering", "PostgreSQL"],
        "excerpt": "Users' 'deleted' data was never deleted: schema.sql promised CASCADE constraints that months of incremental migrations never created. How an end-to-end test against production caught it, and the 12,053 orphaned rows it exposed.",
        "seo_title": "Your schema.sql Is Fiction: The Missing ON DELETE CASCADE That Kept 'Deleted' Data Alive",
        "seo_description": "schema.sql declared ON DELETE CASCADE on every child table. Production tables, built by incremental migrations, had none. Deleted accounts left 12,053 orphaned rows. How a disposable-account e2e test caught what code review couldn't.",
        "seo_keywords": "on delete cascade missing, schema drift production, postgres cascade not working, migration drift, schema.sql vs production, orphaned rows postgres, gdpr delete data postgres",
        "content_html": """
<h2>The setup</h2>
<p>A user filed an issue: "I can't delete my account." Fair — there was no account deletion. GDPR-shaped hole, my fault, so I built it. The store method deleted the <code>users</code> row and trusted the foreign keys: our <code>schema.sql</code> declares <code>ON DELETE CASCADE</code> on every child table. Code review passed. Syntax checked. The SQL was correct.</p>

<h2>The test I almost skipped</h2>
<p>Before shipping, I ran an end-to-end test against production: signed up a disposable account, filled it with real data across every table (facts, events, workflows, embeddings), deleted it through the new endpoint — and then audited every table row-by-row with direct SQL.</p>
<p>Result: <code>api_keys: 1, entities: 4, usage_log: 1</code> — still there.</p>

<h2>The schema file is fiction. The database is fact.</h2>
<p>Production had <strong>no cascade constraints at all</strong>. The schema file declares them — but production tables were created over months by incremental migrations (<code>CREATE TABLE IF NOT EXISTS ...</code>, <code>ALTER TABLE ADD COLUMN ...</code>) that never included the foreign keys. The pristine schema.sql is what a <em>fresh</em> install gets. Production is what history gets.</p>
<p>It got worse. If cascades never worked, what about the regular "delete entity" feature we'd had for months? One audit query across the whole database later:</p>
<p><strong>12,053 orphaned facts. 442 orphaned embeddings.</strong> Every entity deletion since launch had silently left its children behind. Users clicked a button that said "permanently delete" — the parent row vanished, the content stayed on disk, invisible to the API but very much alive.</p>
<p>For a product whose whole pitch is "trust me with your personal memory," that's about the worst class of bug there is.</p>

<h2>The fixes</h2>
<ul>
<li>Deletion is now fully explicit — children before parents, 22 tables, one transaction, zero reliance on cascades. The endpoint returns per-table deletion counts so the user can verify.</li>
<li>Same treatment for single-entity and delete-all paths (they had the same disease).</li>
<li>Second e2e round with a fresh disposable account: zero residue in every table.</li>
</ul>

<h2>Lessons that generalize</h2>
<ol>
<li><strong>Your schema file is fiction. The database is fact.</strong> Audit <code>information_schema.table_constraints</code>, not your repo.</li>
<li><strong>"Syntax OK" and "code review passed" prove nothing about deletion.</strong> Only a row-level audit after a real delete does.</li>
<li><strong>Test destructive paths against the real database</strong> (with a disposable account) — a fresh local install has exactly the constraints your production is missing, so local tests pass for the wrong reason.</li>
</ol>
<p>Check your own prod — this one-liner lists FK constraints and their delete rules:</p>
<pre><code>SELECT conrelid::regclass AS table, conname,
       CASE confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'a' THEN 'NO ACTION'
            WHEN 'r' THEN 'RESTRICT' WHEN 'n' THEN 'SET NULL' END AS on_delete
FROM pg_constraint WHERE contype = 'f' ORDER BY 1;</code></pre>
<p>If what you see doesn't match your schema file — welcome to the club, and go count your orphans.</p>
<p><em>Context: this is Mengram (an AI memory layer) — the account-deletion work, the audit, and both e2e rounds are in the public commit history.</em></p>
""",
    },
    "claude-code-compaction-context-loss": {
        "slug": "claude-code-compaction-context-loss",
        "title": "Claude Code Forgets Everything After Compaction. Here's the Fix That Survives It",
        "date": "July 22, 2026",
        "date_iso": "2026-07-22",
        "read_time": "6",
        "tags": ["Claude Code", "Guide"],
        "excerpt": "Auto-compact wipes your working context — decisions, constraints, even CLAUDE.md guidance drift away. Why it happens, what Anthropic's issue tracker says, and how to make context survive compaction automatically.",
        "seo_title": "Claude Code Forgets Context After Compaction — the Fix That Survives /compact (2026)",
        "seo_description": "Claude Code auto-compaction erases working context: decisions, constraints, project state. 300+ upvotes across GitHub issues confirm it. Here's how persistent memory reloads your context automatically after every compact, /clear, and restart.",
        "seo_keywords": "claude code forgets context, claude code compaction, claude code auto-compact loses context, does claude code remember between sessions, claude code forgets CLAUDE.md, survive compact claude code, claude code context loss fix, claude code persistent memory",
        "content_html": """
<h2>The problem: compaction is amnesia by design</h2>
<p>When a Claude Code session approaches its context limit, <strong>auto-compact</strong> summarizes the conversation and throws away the original. It has to — context windows are finite. But what survives is a summary written under token pressure, and what dies is exactly the stuff you needed: the decision you made an hour ago, the constraint you stated once, the approach you already rejected twice.</p>
<p>This isn't a niche complaint. On Anthropic's own issue tracker: <a href="https://github.com/anthropics/claude-code/issues/17428">enhanced /compact with restorable summaries</a> (114 upvotes), <a href="https://github.com/anthropics/claude-code/issues/27242">no way to review context after compaction</a> (79), <a href="https://github.com/anthropics/claude-code/issues/7502">auto-compact erases chat history without warning</a> (35), and — the quiet killer — <a href="https://github.com/anthropics/claude-code/issues/6354">Claude forgets CLAUDE.md guidance after compaction</a> (28). Hundreds of developers voting on the same wound.</p>

<h2>Why CLAUDE.md doesn't save you</h2>
<p>The standard advice is "put important context in CLAUDE.md." It helps — until it doesn't. CLAUDE.md is static: it holds what you remembered to write down last week, not the decision from forty minutes ago that compaction just ate. And per the issue above, even CLAUDE.md guidance <em>itself</em> loses force after heavy compaction as the summary crowds it out.</p>

<h2>What actually survives: memory outside the context window</h2>
<p>The durable fix is structural: keep the important state <strong>outside</strong> the thing that gets compacted, and re-inject it on every fresh start. Claude Code has the exact machinery for this — the <code>SessionStart</code> hook fires not just on startup, but also on <code>/clear</code>, resume, <em>and after compaction</em>.</p>
<p>That's how the <a href="https://mengram.io">Mengram</a> plugin makes context survive compaction:</p>
<ul>
<li><strong>During the session</strong>, a Stop hook captures each turn in the background — facts, decisions, and workflows get extracted into persistent memory (API keys and tokens are redacted client-side).</li>
<li><strong>After compaction</strong> (or /clear, or a new session, or a different machine), the SessionStart hook reloads your cognitive profile — who you are, what you're building, what you decided — as fresh context. The summary can be lossy; the memory isn't.</li>
<li><strong>On every prompt</strong>, relevant past context is recalled and injected, so "how did we deploy this again?" gets answered from memory instead of re-derived.</li>
</ul>

<h2>Setup (60 seconds)</h2>
<pre><code># 1. Free API key: https://mengram.io — save it once
mkdir -p ~/.mengram && echo '{"api_key": "om-your-key"}' > ~/.mengram/config.json

# 2. Install the plugin
claude plugin marketplace add alibaizhanov/mengram
claude plugin install mengram@mengram

# 3. Optional: feed in your existing session history (secrets redacted locally)
pip install mengram-ai && mengram import claude-code</code></pre>
<p>Test it: tell Claude something about your project, run <code>/compact</code> (or <code>/clear</code>), and ask again. The context comes back — not from the summary, but from memory.</p>

<h2>What this doesn't fix</h2>
<p>Honesty section: no external memory restores the <em>full</em> pre-compact transcript — that's gone, and tools claiming otherwise are re-summarizing too. What persistent memory changes is <strong>which</strong> things survive: instead of whatever the compactor kept under pressure, you keep structured facts, decisions, and workflows extracted while they were fresh. For the transcript itself, vote on <a href="https://github.com/anthropics/claude-code/issues/17428">#17428</a> — file-backed summaries would compose beautifully with external memory.</p>
""",
    },
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
<p>Get an API key at <a href="/#signup">mengram.io</a> and start building. Works with any LLM — OpenAI, Anthropic, Google, open-source models. Also available as an <a href="/blog/mcp-memory-server-setup">MCP server for Claude Desktop</a>.</p>
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
<p>Replace your pure-RAG setup with Mengram in 3 lines: <code>pip install mengram-ai</code>, get an <a href="/#signup">API key</a>, and call <code>m.add()</code> after each conversation. Your AI will start learning from every interaction.</p>
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
        "seo_description": "Step-by-step tutorial: add persistent memory to AI agents in Python or JavaScript. Works with OpenAI, Anthropic, and any LLM. 5-minute setup, plans from $5/mo.",
        "seo_keywords": "add memory to AI agents, AI agent memory tutorial, Python AI memory, JavaScript AI memory, persistent memory for LLMs, Mengram tutorial",
        "content_html": """
<h2>Prerequisites</h2>
<ul>
<li>Python 3.8+ or Node.js 18+</li>
<li>A Mengram API key — <a href="/#signup">get one here</a></li>
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

<p>Get started: <code>pip install mengram-ai</code>, grab an <a href="/#signup">API key</a>, and call <code>m.profile(user_id)</code>. <a href="/blog/how-to-add-memory-to-ai-agents">Full quickstart tutorial here</a>.</p>
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
<p>You need a Mengram API key (<a href="/#signup">get one here</a>) and Claude Desktop installed.</p>

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
        "seo_description": "Detailed comparison of Mem0 vs Mengram for AI memory. Compare memory types, pricing, API design, MCP support, and performance. Open-source Mem0 alternative with 3 memory types.",
        "seo_keywords": "Mem0 vs Mengram, Mem0 alternative, best AI memory tool 2026, Mem0 comparison, AI memory benchmark, open source Mem0 alternative",
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
<tr><td style="padding:10px;">Pricing</td><td style="text-align:center;">$5–99/mo</td><td style="text-align:center;">$19–249/mo</td></tr>
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
<p>Mengram is better if you need: episodic and procedural memory, self-improving workflows, Cognitive Profile for instant personalization, or affordable plans starting at $5/mo. See the <a href="/vs/mem0">full comparison page</a>.</p>
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

<p>Get started: <code>pip install mengram-ai</code> and grab an <a href="/#signup">API key</a>. Full <a href="/blog/how-to-add-memory-to-ai-agents">quickstart tutorial here</a>.</p>
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
        "seo_title": "Persistent Memory for Claude Code in One Command (Hooks Guide)",
        "seo_description": "Install auto-save and auto-recall hooks for Claude Code in one command: every session captured, your profile reloaded on start, secrets redacted locally. Free tier.",
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
    "autonomous-ai-agent-memory": {
        "slug": "autonomous-ai-agent-memory",
        "title": "How to Build AI Agents That Learn From Experience (Persistent Memory Pattern)",
        "date": "March 10, 2026",
        "date_iso": "2026-03-10",
        "read_time": "8",
        "tags": ["Architecture", "Agents"],
        "excerpt": "The memory loop pattern for autonomous AI agents: store outcomes, recall before decisions, evolve procedures from failures. With Python examples.",
        "seo_title": "How to Build AI Agents That Learn From Experience — Persistent Memory Pattern | Mengram",
        "seo_description": "Learn the memory loop pattern for autonomous AI agents. Store outcomes, recall context before decisions, and auto-evolve procedures from failures. Python tutorial with code examples.",
        "seo_keywords": "AI agent persistent memory, autonomous AI agent memory, AI agent learns from experience, agent long-term memory, AI agent persistent state, procedural memory AI agent, AI agent failure learning, agent memory loop",
        "content_html": """
<h2>The problem with stateless agents</h2>
<p>Most AI agents are stateless. They complete a task, the session ends, and everything is gone. Next run, the agent starts from zero — making the same mistakes, trying the same failed approaches, with no memory of what worked before.</p>
<p>This is fine for one-shot tasks. But for <strong>autonomous agents that run repeatedly</strong> — applying to jobs, monitoring systems, processing data, handling support tickets — it's a fundamental limitation. These agents need to <em>learn</em>.</p>

<h2>The memory loop pattern</h2>
<p>The solution is a three-step loop that runs on every agent cycle:</p>

<pre><code>┌─────────────────────────────────────────────┐
│  1. RECALL — search memory before acting    │
│  2. ACT — complete the task                 │
│  3. REMEMBER — store what happened          │
└─────────────────────────────────────────────┘</code></pre>

<p>Over time, the agent accumulates experience. Each run builds on the last. Here's how to implement it:</p>

<h3>Step 1: Recall before acting</h3>
<p>Before your agent starts a task, search memory for relevant context:</p>

<pre><code>from mengram import Mengram

m = Mengram(api_key="om-...")

# Before the agent acts, recall relevant experience
context = m.search_all("submit application on Greenhouse")

# context now contains:
# - Facts: "Greenhouse uses React Select for dropdowns"
# - Episodes: "Application to Acme Corp failed — dropdown selector broke"
# - Procedures: "Greenhouse apply v3: use aria-label selector instead"</code></pre>

<p>The agent now knows what worked before, what failed, and what strategy to use — without any manual prompting.</p>

<h3>Step 2: Act with context</h3>
<p>Pass the recalled context to your agent's LLM as part of the system prompt or tool results. The agent uses this experience to make better decisions:</p>

<pre><code># Inject memory into agent's context
system_prompt = (
    "You are an autonomous agent.\n"
    "Here is what you know from past runs:\n"
    f"{{context}}\n"
    "Use this to avoid repeating past mistakes."
)

# Your agent acts with full context of past experience
response = llm.chat(system_prompt, task_description)</code></pre>

<h3>Step 3: Remember the outcome</h3>
<p>After the agent completes (or fails) the task, store what happened:</p>

<pre><code># Store the outcome — Mengram auto-extracts facts, episodes, and procedures
m.add([
    {{"role": "user", "content": "Apply to Acme Corp on Greenhouse"}},
    {{"role": "assistant", "content": "Applied successfully. Used aria-label selector for dropdowns. Uploaded resume via base64 file input."}},
])</code></pre>

<p>One <code>add()</code> call extracts all three memory types automatically — no manual tagging needed.</p>

<h2>The key: procedures that evolve</h2>
<p>The most powerful part of this pattern is <strong>procedural memory</strong>. When an agent follows a workflow and it fails, the procedure auto-evolves:</p>

<pre><code># Agent tries a procedure and it fails
m.procedure_feedback(proc_id, success=False,
                     context="Dropdown selector broke on Greenhouse")

# Mengram evolves the procedure:
# v1: fill form → submit                           ← FAILED
# v2: fill form → use aria-label selector → submit  ← SUCCESS</code></pre>

<p>Next time the agent encounters the same task, <code>search_all()</code> returns the evolved v2 procedure. The agent improves without any human intervention.</p>

<p>This also happens automatically — just add conversations that mention failures, and Mengram detects the pattern:</p>

<pre><code>m.add([{{"role": "user", "content": "Greenhouse apply failed — dropdown hack stopped working. Switched to aria-label and it worked."}}])
# → Episode created → linked to existing procedure → auto-evolved to v2</code></pre>

<h2>Real-world example: autonomous job application agent</h2>
<p>One of our users built an agent that applies to jobs autonomously. The agent:</p>
<ol>
<li>Discovers job postings matching criteria</li>
<li>Scores them against preferences (role, salary, remote)</li>
<li>Tailors the resume for each position</li>
<li>Submits applications through ATS platforms (Greenhouse, Lever)</li>
<li>Runs 24/7 via cron</li>
</ol>

<p>Without memory, the agent would forget which companies it already applied to, which form-filling strategies work for which platforms, and what workarounds exist for anti-bot measures.</p>

<p>With Mengram, each run makes the agent smarter. After 50+ applications, it has a library of evolved procedures for different ATS platforms, a history of every outcome, and facts about the user's preferences — all searchable in milliseconds.</p>

<h2>The complete agent loop</h2>
<pre><code>from mengram import Mengram

m = Mengram(api_key="om-...")

def agent_loop(task: str, user_id: str = "default"):
    # 1. Recall
    context = m.search_all(task, user_id=user_id)

    # 2. Act (your agent logic here)
    result = your_agent.run(task, context=context)

    # 3. Remember
    m.add([
        {{"role": "user", "content": task}},
        {{"role": "assistant", "content": result}},
    ], user_id=user_id)

    return result

# Run on a schedule — each run builds on the last
while True:
    agent_loop("Check for new jobs and apply to top matches")
    time.sleep(3600)  # every hour</code></pre>

<h2>Works with any framework</h2>
<p>This pattern works with any agent framework:</p>
<ul>
<li><strong>CrewAI</strong> — add Mengram as a tool set (<a href="/blog/ai-memory-for-crewai-langchain">tutorial</a>)</li>
<li><strong>LangChain</strong> — use MengramRetriever + ChatMessageHistory</li>
<li><strong>Claude Code</strong> — auto-memory via hooks (<a href="/blog/claude-code-memory-hooks">setup guide</a>)</li>
<li><strong>Custom loops</strong> — just call <code>add()</code> and <code>search_all()</code></li>
</ul>

<h2>Get started</h2>
<pre><code>pip install mengram-ai</code></pre>
<p>Get an API key at <a href="/#signup">mengram.io</a>. The recall → act → remember loop takes 10 minutes to set up and your agent starts learning from its first run.</p>
""",
        "related": ["how-to-add-memory-to-ai-agents", "semantic-episodic-procedural-memory"],
    },
    "cursor-ai-memory-mcp": {
        "slug": "cursor-ai-memory-mcp",
        "title": "How to Add Persistent Memory to Cursor AI (MCP Setup Guide)",
        "date": "March 18, 2026",
        "date_iso": "2026-03-18",
        "read_time": "6",
        "tags": ["Tutorial", "Cursor", "MCP"],
        "excerpt": "Give Cursor AI persistent memory across sessions. Step-by-step MCP setup so your AI assistant remembers your codebase, preferences, and decisions.",
        "seo_title": "Add Persistent Memory to Cursor: mcp.json Setup in 5 Minutes",
        "seo_description": "Give Cursor memory that survives sessions: the exact ~/.cursor/mcp.json block, the rules paragraph that triggers recall, and how to verify it works.",
        "seo_keywords": "cursor ai memory, cursor persistent memory, cursor mcp server, cursor mcp memory, add memory to cursor, cursor ai remember, cursor context between sessions, cursor long term memory, mcp server cursor setup",
        "content_html": """
<h2>The problem: Cursor forgets everything</h2>
<p>You open Cursor, explain your project architecture, your coding conventions, your deployment setup. Cursor does great work. Then you close the tab.</p>
<p>Next session — Cursor has no idea who you are. You explain everything again. And again. And again.</p>
<p>This is the fundamental limitation of all AI coding assistants: <strong>the context window resets between sessions</strong>. Cursor's context window is large, but it's temporary storage — not memory.</p>

<h2>The fix: persistent memory via MCP</h2>
<p>Cursor supports <strong>MCP (Model Context Protocol)</strong> — a standard for connecting external tools to AI assistants. By connecting a memory MCP server, Cursor can:</p>
<ul>
<li><strong>Remember</strong> your codebase architecture, tech stack, and conventions</li>
<li><strong>Recall</strong> past debugging sessions and what worked</li>
<li><strong>Learn</strong> your coding style and preferences over time</li>
<li><strong>Build</strong> a knowledge graph of your projects, people, and decisions</li>
</ul>
<p>Everything persists across sessions, across devices, forever.</p>

<h2>Setup: 3 minutes</h2>

<h3>Step 1: Get an API key</h3>
<p>Sign up at <a href="/#signup">mengram.io</a> (plans from $5/mo). Copy your API key from the dashboard.</p>

<h3>Step 2: Install the MCP server</h3>
<pre><code>pip install mengram-ai</code></pre>
<p>Or if you prefer npm:</p>
<pre><code>npx mengram-mcp</code></pre>

<h3>Step 3: Configure Cursor</h3>
<p>Open Cursor Settings → MCP Servers → Add new server.</p>
<p>For the pip install method, add this configuration:</p>
<pre><code>{
  "mcpServers": {
    "mengram": {
      "command": "mengram",
      "args": ["server", "--cloud"],
      "env": {
        "MENGRAM_API_KEY": "your-api-key-here"
      }
    }
  }
}</code></pre>

<p>For the npx method:</p>
<pre><code>{
  "mcpServers": {
    "mengram": {
      "command": "npx",
      "args": ["-y", "mengram-mcp"],
      "env": {
        "MENGRAM_API_KEY": "your-api-key-here"
      }
    }
  }
}</code></pre>

<p>Restart Cursor. You should see "mengram" in the MCP tools list.</p>

<h3>Step 4: Start using it</h3>
<p>That's it. Cursor now has 12 memory tools available:</p>
<ul>
<li><code>memory_add</code> — store a conversation or fact</li>
<li><code>memory_search</code> — find relevant past context</li>
<li><code>memory_profile</code> — get a full cognitive profile (system prompt from all memories)</li>
<li><code>memory_list</code> — browse all stored entities</li>
<li><code>memory_graph</code> — explore the knowledge graph</li>
<li><code>memory_stats</code> — see usage stats</li>
<li>...and 6 more for triggers, reflection, import/export, and dedup</li>
</ul>

<h2>What Cursor remembers</h2>
<p>Once connected, Mengram automatically extracts and organizes three types of memory from your conversations:</p>

<h3>Semantic memory (facts)</h3>
<p>Facts about you, your projects, and your preferences:</p>
<ul>
<li>"Uses Next.js 14 with App Router and TypeScript"</li>
<li>"Deploys to Vercel, database on Supabase"</li>
<li>"Prefers functional components over class components"</li>
<li>"Team uses ESLint with Airbnb config"</li>
</ul>

<h3>Episodic memory (events)</h3>
<p>What happened in past sessions:</p>
<ul>
<li>"Debugged a CORS error on March 15 — fixed by adding middleware"</li>
<li>"Migrated from Prisma to Drizzle ORM last week"</li>
<li>"Had a production outage caused by missing env variable"</li>
</ul>

<h3>Procedural memory (workflows)</h3>
<p>Learned step-by-step processes:</p>
<ul>
<li>"To deploy: run tests → build → push to staging → verify → promote to prod"</li>
<li>"When fixing TypeScript errors: check tsconfig first, then look at imported types"</li>
</ul>
<p>Procedural memory <strong>evolves automatically</strong> — when a procedure fails, Mengram updates it with what actually worked. <a href="/blog/semantic-episodic-procedural-memory">Learn more about the three memory types</a>.</p>

<h2>Real example: before and after</h2>

<h3>Without memory (every session)</h3>
<pre><code>You: "Add a new API endpoint for user preferences"
Cursor: "What framework are you using? What's your project structure?
         Where do you put your routes? Do you use TypeScript?"</code></pre>

<h3>With memory (after first session)</h3>
<pre><code>You: "Add a new API endpoint for user preferences"
Cursor: [recalls: Next.js App Router, TypeScript, Supabase, existing route patterns]
        "I'll create app/api/preferences/route.ts following your existing
         pattern with Supabase client and Zod validation..."</code></pre>

<p>No re-explaining. Cursor already knows your stack, your patterns, your preferences.</p>

<h2>Tips for best results</h2>

<h3>1. Tell Cursor to save important context</h3>
<p>After explaining something important, say: <em>"Remember this for future sessions."</em> Cursor will use <code>memory_add</code> to store it permanently.</p>

<h3>2. Ask Cursor to recall before starting work</h3>
<p>At the start of a session, say: <em>"Search your memory for what you know about this project."</em> Cursor will use <code>memory_search</code> to load relevant context.</p>

<h3>3. Use Cognitive Profile for instant context</h3>
<p>Say: <em>"Load my cognitive profile."</em> This generates a complete system prompt from all your stored memories — architecture, preferences, past decisions — in one call.</p>

<h3>4. Let memory build naturally</h3>
<p>You don't need to manually save everything. Over time, the memory builds automatically from your conversations. The more you use Cursor, the smarter it gets.</p>

<h2>Cursor vs Claude Code memory</h2>
<p>Both Cursor and Claude Code support MCP, so the setup is similar. The key difference:</p>
<ul>
<li><strong>Cursor</strong>: MCP tools are available but you manually invoke them (or ask Cursor to use them)</li>
<li><strong>Claude Code</strong>: supports hooks that <a href="/blog/claude-code-memory-hooks">auto-save and auto-recall</a> on every message — fully automatic</li>
</ul>
<p>Both work with the same Mengram backend, so your memories sync across tools.</p>

<h2>Pricing</h2>
<p>Plans start at $5/mo:</p>
<ul>
<li><strong>Starter</strong> ($5/mo) — 100 adds, 500 searches</li>
<li><strong>Pro</strong> ($19/mo) — 1,000 adds, 10,000 searches, smart triggers</li>
<li><strong>Growth</strong> ($59/mo) — 3,000 adds, 20,000 searches, unlimited agents</li>
<li><strong>Business</strong> ($99/mo) — 8,000 adds, 30,000 searches, unlimited teams</li>
</ul>
<p>See <a href="/#pricing">full pricing</a> or <a href="/#signup">get started</a>.</p>

<h2>Get started</h2>
<pre><code>pip install mengram-ai</code></pre>
<p>Get your API key at <a href="/#signup">mengram.io</a>, add the MCP config to Cursor, and your AI assistant starts building permanent memory from the first conversation.</p>
<p>Questions? <a href="https://github.com/alibaizhanov/mengram/issues">Open an issue</a> or reply at <a href="mailto:the.baizhanov@gmail.com">the.baizhanov@gmail.com</a>.</p>
""",
        "related": ["claude-code-memory-hooks", "mcp-memory-server-setup"],
    },
    "context-engineering-memory": {
        "slug": "context-engineering-memory",
        "title": "Context Engineering for AI Agents: Why Memory Is the Missing Piece",
        "date": "April 1, 2026",
        "date_iso": "2026-04-01",
        "read_time": "9",
        "tags": ["Guide", "Architecture"],
        "excerpt": "Context engineering is the new paradigm replacing prompt engineering. But most implementations miss the hardest pillar: persistent memory. Here's how to fix that.",
        "seo_title": "Context Engineering for AI Agents: Why Memory Is the Missing Piece | Mengram",
        "seo_description": "Context engineering has 6 pillars, but most guides skip the hardest one: persistent memory. Learn how semantic, episodic, and procedural memory complete your agent's context stack.",
        "seo_keywords": "context engineering, context engineering AI agents, AI agent memory, context engineering guide, LLM memory, persistent memory, agent context, prompt engineering vs context engineering",
        "content_html": """
<h2>Prompt engineering is dead. Context engineering is here.</h2>
<p>In 2024, every AI tutorial started with "write a better prompt." In 2026, that advice is obsolete. The new paradigm is <strong>context engineering</strong> — designing the entire information environment your AI agent operates in.</p>
<p>The shift makes sense. A prompt is a single instruction. An agent needs an entire world: retrieved documents, tool outputs, conversation history, user preferences, past failures, learned workflows. Managing all of this is context engineering.</p>
<p>But here's the problem: most context engineering guides list 5-6 "pillars" and then hand-wave through the hardest one — <strong>persistent memory</strong>.</p>

<h2>The 6 pillars of context engineering</h2>
<p>Every context engineering framework breaks down into roughly the same components:</p>
<ol>
<li><strong>System prompts</strong> — role, personality, constraints</li>
<li><strong>Retrieval (RAG)</strong> — documents, knowledge bases, vector search</li>
<li><strong>Tools</strong> — APIs, code execution, web access</li>
<li><strong>Conversation history</strong> — the current session's messages</li>
<li><strong>Query augmentation</strong> — rewriting, routing, decomposition</li>
<li><strong>Memory</strong> — persistent knowledge that survives sessions</li>
</ol>
<p>Pillars 1-5 are well-solved. Every framework — LangChain, CrewAI, OpenAI Assistants — has good support for system prompts, RAG, tools, and conversation management.</p>
<p>Pillar 6 is where it falls apart.</p>

<h2>Why memory is the hardest pillar</h2>
<p>Retrieval (RAG) feels like memory, but it isn't. RAG answers "what's in our documents?" Memory answers "what did this agent learn from experience?"</p>
<p>The difference matters when your agent:</p>
<ul>
<li><strong>Repeats the same mistake</strong> — it debugged this exact error yesterday but can't remember</li>
<li><strong>Forgets user preferences</strong> — you told it to use Python and Railway five sessions ago</li>
<li><strong>Can't improve its workflows</strong> — deployment failed, but the procedure doesn't evolve</li>
<li><strong>Loses cross-session continuity</strong> — every session starts from scratch</li>
</ul>
<p>These are not retrieval problems. They're memory problems. And context windows don't solve them — they reset between sessions, and even 200K-token windows suffer from "lost in the middle" degradation.</p>

<h2>The three types of memory your agent needs</h2>
<p>Human cognition uses three distinct memory systems. Effective AI memory mirrors this architecture:</p>

<h3>Semantic memory — facts and knowledge</h3>
<p>What your agent knows about the user, project, and domain. "User is a backend engineer. Uses Python 3.12, PostgreSQL, deploys to Railway."</p>
<p>This is the only type most memory tools implement. It's necessary but not sufficient.</p>

<h3>Episodic memory — events and decisions</h3>
<p>What happened, when, and in what context. "On March 15, deployed v2.3 — Redis cache failed due to OOM, rolled back. Root cause: batch job ran during deployment window."</p>
<p>Episodic memory gives your agent a narrative understanding. Not just what the user knows, but what they've been through.</p>

<h3>Procedural memory — workflows that evolve</h3>
<p>How to do things, learned from experience. This is the rarest and most powerful type:</p>
<pre><code>Week 1:  "Deploy" → build → push → deploy
                                      ↓ FAILURE: forgot migrations
Week 2:  "Deploy" v2 → build → run migrations → push → deploy
                                                         ↓ FAILURE: OOM
Week 3:  "Deploy" v3 → build → run migrations → check memory → push → deploy ✓</code></pre>
<p>Procedural memory captures workflows that <strong>automatically evolve when they fail</strong>. No other memory system does this.</p>

<h2>Context engineering without memory: a broken pipeline</h2>
<p>Let's trace what happens when a developer uses an AI coding agent without persistent memory:</p>

<pre><code># Monday morning — Session 1
Developer: "Set up a FastAPI project with PostgreSQL"
Agent: Creates project from scratch, picks default settings

# Monday afternoon — Session 2
Developer: "Add user authentication"
Agent: Doesn't know the project exists. Asks from scratch.
Developer: Repeats project context. Again.

# Tuesday — Session 3
Developer: "Deploy to Railway"
Agent: No memory of the stack, the auth decisions, or that
       Railway needs a Procfile. Deployment fails.

# Wednesday — Session 4
Developer: "Fix the Railway deployment"
Agent: What Railway deployment? What project?</code></pre>

<p>Every session restarts the context engineering loop from zero. RAG doesn't help because there are no "documents" — just past conversations that should have been remembered.</p>

<h2>Adding memory to the context stack</h2>
<p>With a persistent memory layer, the same workflow transforms:</p>

<pre><code>from mengram import Mengram

m = Mengram(api_key="om-...")

# Before generating any response — load the full context
profile = m.get_profile(user_id="developer-123")
# → "Backend engineer. Python 3.12, FastAPI, PostgreSQL.
#    Deploys to Railway. Recently set up JWT auth.
#    Had OOM issue with Railway — fixed by adding pre-deploy
#    memory check to deployment procedure."

relevant = m.search_all("deployment", user_id="developer-123")
# → semantic: ["Uses Railway with Procfile", "PostgreSQL on Supabase"]
#   episodic: ["Deployment failed Tuesday due to missing migrations"]
#   procedural: ["Deploy v3: build → migrate → check memory → push"]

# Inject into system prompt
system_prompt = f"You are a coding assistant.\\n"
system_prompt += f"Context: {{profile}}\\n"
system_prompt += f"Past experience: {{relevant}}"</code></pre>

<p>Now every session inherits the full context of every previous session. The agent knows the stack, remembers the failures, and follows evolved procedures.</p>

<h2>The Claude Code example: zero-config context engineering</h2>
<p>The most practical implementation of memory-enhanced context engineering is <a href="/blog/claude-code-memory-hooks">Claude Code with Mengram hooks</a>. Two commands:</p>

<pre><code>pip install mengram-ai
mengram setup</code></pre>

<p>This installs three lifecycle hooks:</p>
<ol>
<li><strong>Session start</strong> — loads your cognitive profile (who you are, preferences, tech stack)</li>
<li><strong>Every prompt</strong> — searches past sessions for relevant context before Claude responds</li>
<li><strong>After response</strong> — saves new knowledge in the background</li>
</ol>
<p>No manual saves. No tool calls. Context engineering happens automatically.</p>
<p>The result: Claude Code remembers what you worked on yesterday, what failed, what your deployment process looks like, and what you prefer. Across every session, permanently.</p>

<h2>Architecture: where memory fits in the stack</h2>
<p>Here's how memory integrates with the other context engineering pillars:</p>
<pre><code>┌─────────────────────────────────────────┐
│           Context Assembly              │
│                                         │
│  ┌──────────┐  ┌──────────┐  ┌────────┐│
│  │  System   │  │   RAG    │  │ Tools  ││
│  │  Prompt   │  │ (docs)   │  │ output ││
│  └────┬─────┘  └────┬─────┘  └───┬────┘│
│       │              │            │      │
│       ▼              ▼            ▼      │
│  ┌──────────────────────────────────────┐│
│  │     PERSISTENT MEMORY LAYER          ││
│  │  ┌──────────┬─────────┬───────────┐  ││
│  │  │ Semantic │Episodic │Procedural │  ││
│  │  │ (facts)  │(events) │(workflows)│  ││
│  │  └──────────┴─────────┴───────────┘  ││
│  │  + Cognitive Profile                 ││
│  │  + Cross-session continuity          ││
│  │  + Failure-driven evolution          ││
│  └──────────────────────────────────────┘│
│       │                                  │
│       ▼                                  │
│  ┌──────────────────────────────────────┐│
│  │         LLM Generation               ││
│  └──────────────────────────────────────┘│
└─────────────────────────────────────────┘</code></pre>
<p>Memory isn't a replacement for RAG or tools — it's the layer that ties everything together with persistent, evolving context.</p>

<h2>Implementing memory-first context engineering</h2>
<p>Whether you're building a custom agent or using a framework, the pattern is the same:</p>

<h3>1. Capture: save after every interaction</h3>
<pre><code># After each conversation turn
m.add([
    {{"role": "user", "content": user_message}},
    {{"role": "assistant", "content": agent_response}},
])</code></pre>
<p>Mengram auto-extracts all three memory types from the conversation. No manual tagging.</p>

<h3>2. Recall: search before every response</h3>
<pre><code># Before generating a response
context = m.search_all(user_message)
# Returns semantic facts, relevant episodes, and matching procedures</code></pre>

<h3>3. Personalize: load the cognitive profile</h3>
<pre><code># On session start
profile = m.get_profile()
# Ready-to-use system prompt with everything known about the user</code></pre>

<h3>4. Evolve: let procedures learn from failures</h3>
<pre><code># When a workflow fails
m.procedure_feedback(proc_id, success=False,
                     context="OOM error on step 3", failed_at_step=3)
# Procedure automatically evolves to handle this failure</code></pre>

<p>This four-step loop — capture, recall, personalize, evolve — is the core of memory-first context engineering.</p>

<h2>What changes when memory works</h2>
<p>With persistent memory as part of your context engineering stack:</p>
<ul>
<li><strong>Agents stop repeating mistakes.</strong> Procedural memory captures failures and evolves workflows automatically.</li>
<li><strong>Users stop repeating themselves.</strong> Semantic memory retains preferences, tech stack, and project context across sessions.</li>
<li><strong>Context quality improves over time.</strong> Unlike static RAG, memory gets richer with every interaction.</li>
<li><strong>New sessions start warm.</strong> The cognitive profile gives any LLM instant personalization from day one.</li>
</ul>

<h2>Getting started</h2>
<p>Memory is the missing piece in most context engineering implementations. Adding it takes less than 5 minutes:</p>
<pre><code>pip install mengram-ai</code></pre>
<p>Get your API key at <a href="/#signup">mengram.io</a>. Works with any LLM, any framework. Also available as an <a href="/blog/mcp-memory-server-setup">MCP server</a> and with <a href="/blog/claude-code-memory-hooks">Claude Code hooks</a> for zero-config setup.</p>
<p>The question isn't whether your agent needs memory. It's how long you can afford to operate without it.</p>
""",
        "related": ["what-is-ai-memory", "claude-code-memory-hooks"],
    },
    "claude-managed-agents-memory": {
        "slug": "claude-managed-agents-memory",
        "title": "Add Persistent Memory to Claude Managed Agents with Mengram",
        "date": "April 9, 2026",
        "date_iso": "2026-04-09",
        "read_time": "6",
        "tags": ["Tutorial", "Managed Agents"],
        "excerpt": "Give your Claude Managed Agents long-term memory across sessions. Connect Mengram via MCP in 2 minutes — your agents remember users, learn from failures, and build cognitive profiles.",
        "seo_title": "Add Persistent Memory to Claude Managed Agents | Mengram",
        "seo_description": "Step-by-step guide to adding persistent memory to Anthropic's Claude Managed Agents using Mengram's MCP server. Semantic, episodic, and procedural memory for autonomous agents.",
        "seo_keywords": "Claude Managed Agents memory, Managed Agents MCP, Anthropic Managed Agents persistent memory, Claude agent memory, Managed Agents long-term memory, Mengram Managed Agents",
        "content_html": """
<h2>What are Claude Managed Agents?</h2>
<p><a href="https://docs.anthropic.com/en/docs/agents/managed-agents">Claude Managed Agents</a> is Anthropic's hosted platform for running autonomous AI agents. Launched in April 2026, it lets you define agents with custom tools, instructions, and MCP servers — then run them via API without managing infrastructure.</p>
<p>But Managed Agents start every session from scratch. They don't remember past conversations, user preferences, or lessons learned. That's where Mengram comes in.</p>

<h2>Why agents need memory</h2>
<p>Without memory, your agent:</p>
<ul>
<li>Asks the same onboarding questions every session</li>
<li>Repeats mistakes it already solved</li>
<li>Can't personalize responses based on past interactions</li>
<li>Loses context between runs — each session is isolated</li>
</ul>
<p>With Mengram, your agent gets <strong>3 types of memory</strong>:</p>
<ul>
<li><strong>Semantic</strong> — facts, preferences, knowledge ("uses Python, deploys to Railway")</li>
<li><strong>Episodic</strong> — events and outcomes ("deployment crashed on March 5, fixed by adding migrations")</li>
<li><strong>Procedural</strong> — workflows that evolve from failures ("deploy v3: build → migrate → check memory → push")</li>
</ul>

<h2>Connect Mengram to Managed Agents</h2>
<p>Managed Agents support remote MCP servers via HTTP transport. Mengram's cloud MCP endpoint works out of the box.</p>

<h3>Step 1: Get a Mengram API key</h3>
<p>Sign up at <a href="/#signup">mengram.io</a> — plans from $5/mo. You'll get an API key starting with <code>om-</code>.</p>

<h3>Step 2: Add Mengram as an MCP server</h3>
<p>In your Managed Agent definition, add Mengram's MCP endpoint:</p>
<pre><code>{{
  "name": "my-agent",
  "model": "claude-sonnet-4-6",
  "instructions": "You are a helpful assistant with persistent memory.",
  "mcp_servers": [
    {{
      "type": "url",
      "name": "mengram",
      "url": "https://mengram.io/mcp"
    }}
  ],
  "tools": [
    {{
      "type": "agent_toolset_20260401",
      "default_config": {{
        "enabled": true,
        "permission_policy": {{"type": "always_allow"}}
      }}
    }},
    {{
      "type": "mcp_toolset",
      "mcp_server_name": "mengram",
      "default_config": {{
        "enabled": true,
        "permission_policy": {{"type": "always_allow"}}
      }}
    }}
  ]
}}</code></pre>
<p><strong>Important:</strong> Set <code>permission_policy</code> to <code>always_allow</code> for the MCP toolset. The default (<code>always_ask</code>) requires manual tool confirmation — without it, memory tool calls will time out.</p>

<h3>Step 3: Store your API key in a vault</h3>
<p>Managed Agents use <a href="https://docs.anthropic.com/en/docs/agents/managed-agents#vaults">vaults</a> for secrets. Create a vault, add your Mengram API key as a <code>static_bearer</code> credential, then reference the vault when creating a session:</p>
<pre><code>import anthropic

client = anthropic.Anthropic()

# Create a vault for this user
vault = client.beta.vaults.create(display_name="My User")

# Add Mengram API key as a credential
client.beta.vaults.credentials.create(
    vault_id=vault.id,
    display_name="Mengram Memory",
    auth={{
        "type": "static_bearer",
        "mcp_server_url": "https://mengram.io/mcp",
        "token": "om-your-mengram-api-key",
    }},
)

# Create an environment and session
env = client.beta.environments.create(display_name="Default")
session = client.beta.sessions.create(
    agent=agent.id,
    vault_ids=[vault.id],
    environment_id=env.id,
)</code></pre>

<h2>What your agent gets</h2>
<p>Once connected, your Managed Agent has access to <strong>29 memory tools</strong>:</p>
<table style="width:100%; border-collapse:collapse; font-size:14px; margin:20px 0;">
<thead>
<tr style="border-bottom:1px solid #1a1a2e;">
<th style="padding:10px; text-align:left; color:#9898b0;">Tool</th>
<th style="padding:10px; text-align:left; color:#9898b0;">What it does</th>
</tr>
</thead>
<tbody>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;"><code>remember</code></td><td style="padding:10px;">Save conversation to memory — auto-extracts facts, events, procedures</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;"><code>recall</code></td><td style="padding:10px;">Semantic search through past memories</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;"><code>search_all</code></td><td style="padding:10px;">Unified search across all 3 memory types</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;"><code>context_for</code></td><td style="padding:10px;">Get relevant context pack for a specific task</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;"><code>list_procedures</code></td><td style="padding:10px;">Retrieve learned workflows with success/failure tracking</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;"><code>procedure_feedback</code></td><td style="padding:10px;">Report outcomes — on <code>success=false</code> with a <code>context</code>, the procedure evolves into a revised version (cloud backend)</td></tr>
<tr><td style="padding:10px;"><code>reflect</code></td><td style="padding:10px;">Trigger AI reflection to find patterns across memories</td></tr>
</tbody>
</table>
<p>Plus 22 more — entity management, knowledge graph, triggers, dedup, import/export, and more. <a href="/docs/mcp-server">Full tool reference</a>.</p>

<h2>Mengram vs Anthropic's Memory Stores</h2>
<p>Managed Agents have built-in <a href="https://docs.anthropic.com/en/docs/agents/memory-stores">Memory Stores</a> (research preview). Here's how they compare:</p>
<table style="width:100%; border-collapse:collapse; font-size:14px; margin:20px 0;">
<thead>
<tr style="border-bottom:1px solid #1a1a2e;">
<th style="padding:10px; text-align:left; color:#9898b0;">Feature</th>
<th style="padding:10px; text-align:center; color:#a855f7; font-weight:600;">Mengram</th>
<th style="padding:10px; text-align:center; color:#9898b0;">Memory Stores</th>
</tr>
</thead>
<tbody>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Memory types</td><td style="text-align:center;"><strong>3</strong> (semantic + episodic + procedural)</td><td style="text-align:center;">1 (text documents)</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Auto-extraction from conversations</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x274C; (manual text)</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Procedural learning (evolving workflows)</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x274C;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Cognitive Profile</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x274C;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Knowledge graph</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x274C;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Semantic search</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">&#x2705;</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Multi-user isolation</td><td style="text-align:center;">&#x2705;</td><td style="text-align:center;">Per-agent only</td></tr>
<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:10px;">Works beyond Anthropic</td><td style="text-align:center;">&#x2705; (any LLM)</td><td style="text-align:center;">&#x274C; (Managed Agents only)</td></tr>
<tr><td style="padding:10px;">Status</td><td style="text-align:center;"><strong>Production</strong></td><td style="text-align:center;">Research preview</td></tr>
</tbody>
</table>
<p>Memory Stores are simple text documents — you manually write and retrieve text. Mengram automatically extracts structured knowledge from conversations and builds a knowledge graph, cognitive profiles, and self-improving procedures.</p>

<h2>Example: Support agent with memory</h2>
<pre><code>import anthropic

client = anthropic.Anthropic()

# Create an agent with Mengram memory
agent = client.beta.agents.create(
    name="support-agent",
    model="claude-sonnet-4-6",
    instructions="You are a customer support agent with persistent memory. "
        "At the start of each conversation: "
        "1) Use recall() to search for the customer's past interactions. "
        "2) Use context_for() to get relevant procedures and knowledge. "
        "After resolving issues: "
        "1) Use remember() to save the conversation. "
        "2) Use procedure_feedback() to report success/failure. "
        "This way you learn from every interaction and never ask the same question twice.",
    mcp_servers=[
        {{
            "type": "url",
            "name": "mengram",
            "url": "https://mengram.io/mcp"
        }}
    ],
    tools=[
        {{
            "type": "agent_toolset_20260401",
            "default_config": {{
                "enabled": True,
                "permission_policy": {{"type": "always_allow"}}
            }}
        }},
        {{
            "type": "mcp_toolset",
            "mcp_server_name": "mengram",
            "default_config": {{
                "enabled": True,
                "permission_policy": {{"type": "always_allow"}}
            }}
        }}
    ]
)

# Store Mengram API key in a vault
vault = client.beta.vaults.create(display_name="Customer")
client.beta.vaults.credentials.create(
    vault_id=vault.id,
    display_name="Mengram Memory",
    auth={{
        "type": "static_bearer",
        "mcp_server_url": "https://mengram.io/mcp",
        "token": "om-your-mengram-api-key",
    }},
)

# Create environment and session
env = client.beta.environments.create(display_name="Support")
session = client.beta.sessions.create(
    agent=agent.id,
    vault_ids=[vault.id],
    environment_id=env.id,
)

# Send a message — agent recalls past context automatically
client.beta.sessions.events.send(
    session_id=session.id,
    events=[{{
        "type": "user.message",
        "content": [{{"type": "text", "text": "I'm having trouble with my deployment again"}}]
    }}]
)</code></pre>

<h2>Pricing</h2>
<p>Plans start at <strong>$5/month</strong> (Starter) with 100 adds and 500 searches. Less than your morning coffee. <a href="/#pricing">See all plans</a>.</p>

<h2>Get started</h2>
<ol>
<li>Get an API key at <a href="/#signup">mengram.io</a></li>
<li>Add the MCP config to your Managed Agent definition</li>
<li>Store your API key in a vault</li>
<li>Your agent now has persistent memory across sessions</li>
</ol>
<p>Full documentation: <a href="/docs/managed-agents">Managed Agents integration guide</a> · <a href="/docs/mcp-server">MCP server reference</a> · <a href="/docs/agent-memory">Agent memory concepts</a></p>
""",
        "related": ["mcp-memory-server-setup", "how-to-add-memory-to-ai-agents"],
    },
    "multi-tenant-mcp-server": {
        "slug": "multi-tenant-mcp-server",
        "title": "Multi-Tenant MCP Servers: How to Add user_id Isolation to Model Context Protocol",
        "date": "April 22, 2026",
        "date_iso": "2026-04-22",
        "read_time": "8",
        "tags": ["MCP", "Multi-Tenant", "Tutorial"],
        "excerpt": "MCP servers are single-user by default. Here's why that breaks when you build SaaS on top of them — and the exact one-argument fix that makes every tool multi-tenant without breaking backward compatibility.",
        "seo_title": "Multi-Tenant MCP Servers: Add user_id Isolation to MCP | Mengram",
        "seo_description": "How to add multi-tenant user isolation to any Model Context Protocol (MCP) server. Two-tier identity model, working code, and the design decisions behind scoped tool calls.",
        "seo_keywords": "mcp multi tenant, mcp user_id, mcp multi user, model context protocol multi tenant, mcp server user isolation, mcp sub user, claude desktop multi user",
        "content_html": """
<h2>The bug that lived in plain sight</h2>

<p>Last week, <a href="https://github.com/alibaizhanov/mengram/discussions/30">a developer opened a discussion</a> on our repo with a simple question: "Can you add multi-user support to the MCP server like the REST API has?"</p>

<p>We thought we already had it. We were wrong.</p>

<p>The REST API had multi-user isolation baked in from day one — pass <code>user_id</code> in the request body, memories get scoped per end-user. The Python and JavaScript SDKs inherited it. But the MCP server — which is how Claude Desktop, Cursor, Windsurf, and a growing list of AI-native IDEs talk to memory — was half-wired. Some tools respected <code>user_id</code>. Fourteen did not.</p>

<p>This post walks through why multi-tenancy matters for MCP, the specific design we used to fix it without breaking backward compatibility, and the exact code change so you can do the same in your own MCP server.</p>

<h2>Why MCP is single-user by default</h2>

<p>The <a href="https://modelcontextprotocol.io/">Model Context Protocol</a> was designed for a personal AI assistant on your laptop. The mental model is simple: one user, one server, one scope of memory. That's fine for <code>filesystem</code> or <code>github</code> MCP servers — they operate on resources you personally own.</p>

<p>But memory is different. The moment you ship an MCP server that wraps a SaaS backend, every request arrives with the same credential (your API key), and the server has no way to know which human the query is <em>about</em>.</p>

<p>Concretely: imagine you run a customer-support platform. Your AI agent — running through <a href="/blog/claude-managed-agents-memory">Claude Managed Agents</a>, Claude Desktop, or a Cursor workflow — handles tickets for thousands of end-users. If your memory MCP server stores everything under the API key owner's scope, you end up with one giant bucket of facts where Alice's allergies, Bob's deployment history, and Carol's billing preferences are mixed together. Search for "Alice" and you might get results from another customer who mentioned her name in passing.</p>

<p>That's not a memory layer. That's a leak.</p>

<h2>The two-tier model: tenant + end-user</h2>

<p>The fix — already widely used in B2B SaaS but not yet baked into MCP conventions — is a <strong>two-tier identity model</strong>:</p>

<ol>
<li><strong>Tenant</strong> (from the API key): "Who is paying for this?"</li>
<li><strong>End-user</strong> (from the request): "Which of this tenant's users is this about?"</li>
</ol>

<p>MCP doesn't define how to carry the second tier. The transport authenticates once via a bearer token, and every tool call inherits that scope. To add end-user identity, you have to pass it inside the tool arguments.</p>

<p>Here's the pattern we settled on:</p>

<pre><code>// Every tool accepts an optional user_id argument.
// Without it: use the API key owner's default scope.
// With it: scope the operation to that end-user.

{{
  "name": "remember",
  "arguments": {{
    "conversation": [
      {{"role": "user", "content": "Alice prefers dark mode"}}
    ],
    "user_id": "alice"
  }}
}}</code></pre>

<p>If <code>user_id</code> is absent, the tool falls back to the default scope — identical behavior to before we shipped this. Zero breakage for existing clients. Explicit opt-in for multi-tenant use.</p>

<h2>The code change, exactly</h2>

<p>Here's what the fix looks like in the MCP server. Before:</p>

<pre><code>@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "remember":
        result = mem.add(arguments["conversation"], user_id=user_id)
        # ^^^^^^^^^^^^^^^^^^ hardcoded to server default
        return [TextContent(type="text", text=format(result))]</code></pre>

<p>After:</p>

<pre><code>@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if "user_id" in arguments:
        print(f"[mcp] user_id override: tool={{name}} "
              f"sub_uid={{arguments['user_id']}}", file=sys.stderr)

    if name == "remember":
        uid = arguments.get("user_id", user_id)  # fallback to default
        result = mem.add(arguments["conversation"], user_id=uid)
        return [TextContent(type="text", text=format(result))]</code></pre>

<p>Three line changes per tool. One log line at the top to give you a clean audit trail when end-users are explicitly scoped — invaluable when debugging a customer report of "my users see each other's data."</p>

<p>You also need to declare <code>user_id</code> in each tool's <code>inputSchema</code> so the MCP client can advertise it:</p>

<pre><code>Tool(
    name="remember",
    description="Save knowledge from a conversation.",
    inputSchema={{
        "type": "object",
        "properties": {{
            "conversation": {{"type": "array", "items": {{...}}}},
            "user_id": {{
                "type": "string",
                "description": "Optional user ID override"
            }},
        }},
        "required": ["conversation"],
    }},
)</code></pre>

<p>That's the entire design. Fourteen tools at Mengram needed this change. We shipped it as v2.23.0 and deployed to production the same day.</p>

<h2>Alternatives we considered</h2>

<h3>1. A separate API key per tenant</h3>
<p>"Just make each customer use a different API key." This works but only solves tier 1. The end-user tier is still flat. Also creates a rotation nightmare if one customer has 50,000 end-users, and every key rotation means touching every MCP client config.</p>

<h3>2. HTTP header for user_id</h3>
<p>Technically possible on the streamable HTTP transport, but MCP clients (Claude Desktop, Cursor, etc.) don't let you add per-call headers. Tool arguments are the only channel the MCP spec guarantees across stdio, SSE, and streamable HTTP.</p>

<h3>3. One MCP server process per end-user</h3>
<p>Spawn-on-login works for a dozen users. Breaks past 100. Memory cost is linear, and you lose the ability to have a single persistent connection pool to your backend.</p>

<h3>4. Encode user_id into the API key</h3>
<p>"Use <code>apikey-alice</code>, <code>apikey-bob</code>." Nope — tenants don't want to provision a separate key per end-user. Plus the key becomes a security-sensitive identifier instead of an authentication secret.</p>

<p>The two-tier model with <code>user_id</code> in tool arguments is the only approach that scales, stays backward-compatible, and works across every MCP transport.</p>

<h2>What you get with multi-tenant MCP</h2>

<p>Once every tool accepts <code>user_id</code>, you can build things that were impossible with a flat namespace:</p>

<ul>
<li><strong>Per-user cognitive profiles</strong> — Alice's system prompt is not Bob's.</li>
<li><strong>Scoped search</strong> — <code>search("allergies", user_id="alice")</code> only returns Alice's data, even if Bob once mentioned her name.</li>
<li><strong>Per-user procedures</strong> — Alice's deployment workflow evolves independently from Bob's. See <a href="/blog/semantic-episodic-procedural-memory">procedural memory</a>.</li>
<li><strong>Per-user triggers</strong> — contradictions and reminders fire only for the right person.</li>
<li><strong>Compliant data deletion</strong> — when a user leaves your platform, delete just their scope. GDPR Article 17 becomes a one-line operation instead of a database surgery.</li>
</ul>

<h2>A note on security</h2>

<p>The two-tier model puts the burden of passing the <em>correct</em> <code>user_id</code> on the API caller. If your agent code accidentally mixes up <code>user_id</code> values between requests, you've leaked data. This is the same trust model as every multi-tenant SaaS, but it's worth stating explicitly.</p>

<p>Mitigations we recommend:</p>

<ul>
<li>Derive <code>user_id</code> from your session/auth layer at the <em>agent</em> level, not from LLM output. The LLM should never choose which user's data to query.</li>
<li>Log every <code>user_id</code> override at your MCP server (see the <code>print</code> line above) so you have an audit trail.</li>
<li>Enforce a server-side allowlist of valid <code>user_id</code> values per API key if your threat model is strict.</li>
</ul>

<h2>Try it</h2>

<p>Mengram's cloud MCP server now exposes multi-user scoping on every tool. Point your MCP client at the streamable HTTP endpoint, include <code>user_id</code> in your tool calls, and memories stay isolated per end-user:</p>

<pre><code>Endpoint:  https://mengram.io/mcp
Auth:      Authorization: Bearer &lt;MENGRAM_API_KEY&gt;
Discovery: https://mengram.io/.well-known/mcp</code></pre>

<p>Full reference: <a href="/docs/mcp-server">MCP server docs</a>. The original discussion thread that kicked this off: <a href="https://github.com/alibaizhanov/mengram/discussions/30">discussions/30</a>. Shipped in v2.23.0.</p>

<p>If you're building an MCP server yourself and want to add multi-tenancy, or if you hit gotchas we missed, open a <a href="https://github.com/alibaizhanov/mengram/discussions">discussion on the repo</a>. The more MCP servers adopt this pattern, the easier it becomes for everyone downstream to build multi-user agents.</p>
""",
        "related": ["mcp-memory-server-setup", "claude-managed-agents-memory"],
    },
    "multilingual-ai-memory": {
        "slug": "multilingual-ai-memory",
        "title": "Multilingual AI Memory: How Mengram Retrieves in 23 Languages (and Why English-Only Memory Fails)",
        "date": "May 4, 2026",
        "date_iso": "2026-05-04",
        "read_time": "9",
        "tags": ["Multilingual", "Embeddings", "Architecture"],
        "excerpt": "Most AI memory layers — Mem0, Letta, Zep — use OpenAI embeddings. OpenAI embeddings are English-biased. So when your agent's user writes in Russian, Spanish, or Chinese, retrieval quality silently collapses. Here's how we fixed it with Cohere multilingual-v3 — and why \"native multilingual\" is more than translating queries.",
        "seo_title": "Multilingual AI Memory: 23 Languages, Cross-Lingual Search | Mengram",
        "seo_description": "Most AI memory tools are English-biased because they use OpenAI embeddings. Mengram uses Cohere multilingual-v3 — equal retrieval quality across 23+ languages, cross-lingual search built in. How and why.",
        "seo_keywords": "multilingual AI memory, AI memory non-English, cross-lingual retrieval, Cohere multilingual embeddings, AI memory Russian, AI memory Spanish, AI memory Chinese, multilingual LLM memory, agent memory non English",
        "content_html": """
<h2>The bug nobody talks about</h2>

<p>Last month, a developer in Mexico opened a support ticket with us. He had built a customer-service agent on top of Mengram for a Spanish-speaking SaaS. Things were working — until they weren't. Customers said something in Spanish; the agent retrieved facts in English from previous chats; relevance scores looked fine on paper but the answers were drifting, sometimes badly.</p>

<p>It wasn't his code. It was the embedding model under the hood — the same model nearly every AI memory layer ships with by default.</p>

<p>OpenAI's <code>text-embedding-3-large</code> is excellent for English. It's mediocre for Russian, Chinese, and Arabic. It's worse than mediocre for cross-lingual search — when your query is in one language and your stored memory is in another. <a href="https://huggingface.co/spaces/mteb/leaderboard">MTEB leaderboard data</a> confirms this: on the MIRACL multilingual benchmark, OpenAI's flagship embeddings score 54.9; Cohere's <code>embed-multilingual-v3</code> scores 67.0 on the same task. That's a 22% relative quality gap on the exact problem AI agents face every day in non-English markets.</p>

<p>Most AI memory layers (Mem0, Letta, Zep, MemGPT) use OpenAI by default. So if you build a memory-enabled agent for users outside the English internet, retrieval quality silently collapses. Customers don't get the right context. The agent looks dumb. You blame the LLM.</p>

<p>It's not the LLM. It's the embedding step that runs <em>before</em> the LLM ever sees the memory.</p>

<h2>What "native multilingual" actually means</h2>

<p>There are three architectures people call "multilingual," only one of which actually works:</p>

<h3>1. Translate-then-embed (broken)</h3>
<p>Take Russian input → run it through GPT translation → embed the English version → store. At query time: translate query → search English vectors. Two extra LLM calls per operation, latency triples, and translation introduces semantic drift. Compound that across thousands of memories, and search quality is worse than just using a multilingual model directly. Several wrappers do this and call themselves "multilingual." They're not — they're "auto-translating," which is different.</p>

<h3>2. One model per language (broken at scale)</h3>
<p>Maintain separate vector indexes per language. Detect input language, route to the matching index. This works inside a single language but breaks the moment a user mixes languages in one conversation (which they do constantly — code-switching, English technical terms inside non-English prose, brand names). And cross-lingual search ("query in English, find Russian memories") becomes impossible.</p>

<h3>3. Native multilingual embeddings (the actual answer)</h3>
<p>One model, one vector space, semantically equivalent text in <em>any</em> language maps to nearby vectors. "I love coffee" in English and "Я люблю кофе" in Russian land within ~0.1 cosine distance. The model was trained from scratch on multilingual text, not retrofitted with translation. This is what Cohere's <code>embed-multilingual-v3</code> does — and it's why we migrated Mengram's entire embedding pipeline to it earlier this year.</p>

<h2>How Mengram does it now</h2>

<p>Every fact, episode, and procedure stored in Mengram gets a 1024-dim vector from <code>cohere.embed-multilingual-v3.0</code>. Same model for the input query. PostgreSQL with pgvector indexes the result. There is no translation step. There are no per-language partitions. There is one vector space, and it speaks 100+ languages — we test against 23 of the most common to set quality SLAs (Russian, Mandarin Chinese, Spanish, Portuguese, French, German, Italian, Polish, Japanese, Korean, Arabic, Hindi, Bengali, Tamil, Turkish, Vietnamese, Thai, Indonesian, Dutch, Hebrew, Greek, Czech, English).</p>

<p>That's the whole feature. There's no language flag in the API. You don't tell Mengram what language your input is in. It just works:</p>

<pre><code># Store a memory in Russian
m.add([{{"role": "user", "content": "я фронтенд-разработчик в Stripe, переезжаю в Сан-Франциско"}}])

# Search in English — still finds the Russian memory
results = m.search("Where does the user live?")
# → returns: "переезжаю в Сан-Франциско" (San Francisco), score 0.84

# Or search in Russian — finds the same fact
results = m.search("Где живёт пользователь?")
# → score 0.91 (slightly higher because same-language query is always tighter)
</code></pre>

<p>Cross-lingual works because in a properly trained multilingual embedding space, the <em>concept</em> of "moving to San Francisco" is encoded the same way regardless of the surface language used to express it. The query and the document don't need to share words. They share meaning.</p>

<h2>What this enables</h2>

<p>The interesting part isn't the benchmark number. It's the use cases that were impossible before:</p>

<h3>Customer service across languages</h3>
<p>A SaaS based in Berlin has English-, German-, and Turkish-speaking customers. Each customer's history is stored in whatever language they wrote it. When a German customer reaches out and the agent searches for "billing issues," it pulls relevant memories from <em>all</em> their conversations — including the ones in Turkish or English. No language filter, no translation pipeline.</p>

<h3>Code-switching in real conversations</h3>
<p>Half the world's developers write code-switched: "Я делаю refactor на FastAPI, но <code>SQLAlchemy session</code> теряется." Translate-then-embed pipelines mangle this kind of input badly. Native multilingual embeddings handle it as one continuous semantic stream — exactly the way the writer thinks.</p>

<h3>Multilingual agents without infrastructure</h3>
<p>Without multilingual memory, building a non-English AI agent means provisioning per-language Pinecone indexes, writing language detection routers, maintaining translation fallbacks, and praying the latencies stay reasonable. With native multilingual memory, you point your agent at one Mengram endpoint and it works for every user, every language, every topic. The infrastructure complexity collapses to zero.</p>

<h3>The non-English long tail</h3>
<p>About 75% of internet users live outside the English-first AI ecosystem. The vast majority of AI memory tools optimize for the 25%. Mengram is the rare exception that benchmarks <em>against</em> the long tail — Russian, Indonesian, Tamil, Hebrew — and treats English as one of 23 supported languages, not the default.</p>

<h2>The trade-off</h2>

<p>Cohere multilingual embeddings are not free, and they're not always better than OpenAI for English-only workloads. On purely English benchmarks, OpenAI's <code>text-embedding-3-large</code> wins by a small margin. If your agent only ever sees English text, you don't need multilingual.</p>

<p>The moment a single non-English user shows up — or a single English user pastes a Russian quote, a Chinese product name, a Spanish customer review — the math flips. Cohere multilingual handles English well enough (within 2-3% of OpenAI on English MTEB) <em>and</em> dominates on everything else. For a memory layer that has to work across an unknown user population, that's the right trade.</p>

<p>Cohere also charges per token, like OpenAI. Costs are comparable; we measured ~$0.10 per million input tokens on production load.</p>

<h2>Why most memory tools won't switch</h2>

<p>Switching embedding models is not a config change. It's a data migration. Every existing vector in the database was computed with the old model and is incompatible with the new one. You either re-embed everything (expensive, slow, requires zero-downtime dual-write logic) or you partition by model version and route queries (complex, breaks cross-fact relevance).</p>

<p>We did the migration in March of this year — dual-column schema (<code>embedding</code> for OpenAI 1536-dim, <code>embedding_v2</code> for Cohere 1024-dim), background backfill of 81,499 vectors at $0.84 total cost, atomic cutover. Took about a week of careful work. Most memory startups won't do this until enough non-English customers complain to make it a P0. We did it pre-emptively because every customer of ours who wasn't on English support was getting silently mediocre results.</p>

<h2>Try it</h2>

<p>If you're building an agent that touches more than one language — or if you've been blaming your LLM for retrieval failures that are actually embedding failures — try the <a href="/#playground">Mengram playground</a> with non-English input. Add a fact in Spanish or Russian, search in English, see what comes back.</p>

<p>If retrieval quality matters to you and you've been quietly working around a memory layer that doesn't speak your users' languages, you're not alone. Most of our customers come from exactly that frustration. Reach out at <a href="mailto:ali@mengram.io">ali@mengram.io</a> if you want to compare benchmarks on your specific language pair before migrating.</p>

<p>The internet is not English-first anymore. Memory layers should match.</p>
""",
        "related": ["semantic-episodic-procedural-memory", "ai-memory-vs-rag", "how-to-add-memory-to-ai-agents"],
    },
    "openai-agent-builder-memory": {
        "slug": "openai-agent-builder-memory",
        "title": "Add Memory to OpenAI Agent Builder in 2 Minutes (via OpenAPI)",
        "date": "May 4, 2026",
        "date_iso": "2026-05-04",
        "read_time": "5",
        "tags": ["OpenAI", "Tutorial", "Integration"],
        "excerpt": "OpenAI Agent Builder gives you a visual canvas for AI agents — tools, knowledge, logic — but no persistent memory between sessions. Here's how to plug Mengram in via OpenAPI in two minutes, including auth, multi-user scoping, and the three tool calls that matter.",
        "seo_title": "Add Memory to OpenAI Agent Builder via OpenAPI | Mengram",
        "seo_description": "Step-by-step: import Mengram's OpenAPI spec into OpenAI Agent Builder or a Custom GPT, configure Bearer auth, and give your agent persistent memory in 2 minutes. Works with Custom GPTs and Assistants API too.",
        "seo_keywords": "openai agent builder memory, custom gpt memory, openai agent persistent memory, agent builder long term memory, custom gpt actions openapi, openai assistants memory, agent builder mengram",
        "content_html": """
<h2>The gap in Agent Builder</h2>

<p>OpenAI Agent Builder is a visual canvas for assembling agents — drag in an LLM node, attach tools, wire up knowledge files, set logic branches, publish as a chat widget. It is excellent at the prototyping layer. There's just one thing it doesn't have: <strong>persistent memory between sessions</strong>.</p>

<p>Knowledge files in Agent Builder are static. They're embedded once and queried as a RAG store. They don't grow with the conversation. They don't track what the user told the agent yesterday. They don't capture the workflows the agent figured out three runs ago. The moment a user closes the chat, the agent forgets everything that wasn't already in the knowledge base before launch.</p>

<p>That's a problem if you're building anything beyond a one-shot Q&amp;A. Customer support agents need to remember repeat customers. Sales assistants need to track who's been pitched what. Coaching apps need session continuity. None of that fits in static knowledge files.</p>

<p>Mengram solves it via <a href="/blog/semantic-episodic-procedural-memory">three memory types</a> exposed as a REST API. Agent Builder accepts external tools via OpenAPI imports. So the integration is a paste-and-go.</p>

<h2>The 2-minute setup</h2>

<h3>Step 1 — Get an API key</h3>
<p>Sign up at <a href="https://mengram.io/#signup">mengram.io</a>. The free tier gets you 40 add operations and 200 searches per month — enough to validate the integration. Copy the key from your dashboard. It looks like <code>om-...</code>.</p>

<h3>Step 2 — Import the OpenAPI spec</h3>
<p>In Agent Builder, add a <strong>Custom Tool</strong> (or in a Custom GPT, go to <em>Configure → Actions → Create new action</em>). Paste this URL into the schema importer:</p>

<pre><code>https://mengram.io/openapi.json</code></pre>

<p>OpenAI fetches the spec, lists all 66 endpoints, and you pick which ones to expose to your agent. For most use cases, you only need three:</p>

<ul>
<li><code>POST /v1/add</code> — save a conversation snippet to memory</li>
<li><code>POST /v1/search/all</code> — unified search across semantic, episodic, and procedural memory</li>
<li><code>GET /v1/profile</code> — get the cognitive profile (a generated system-prompt summary of everything known about the user)</li>
</ul>

<p>Skip the dashboard / billing / signup endpoints — your agent doesn't need them.</p>

<h3>Step 3 — Configure authentication</h3>
<p>Authentication is Bearer token. In Agent Builder's auth panel:</p>

<ul>
<li><strong>Authentication type:</strong> API Key</li>
<li><strong>Auth Type:</strong> Bearer</li>
<li><strong>API Key:</strong> paste your <code>om-...</code> key</li>
</ul>

<p>That's it. The OpenAPI spec already declares the auth scheme; OpenAI just needs your token to send.</p>

<h3>Step 4 — Wire it into your agent prompt</h3>
<p>In your agent's system prompt or the LLM node's instructions, tell it when to use memory. Something like:</p>

<pre><code>Before answering the user, call /v1/profile to load their cognitive profile.
Use /v1/search/all to find relevant past context for any specific question.
After meaningful exchanges, call /v1/add to save the conversation.

Pass user_id={{customer_id}} on every call to scope memories per end-user.</code></pre>

<p>The <code>{{customer_id}}</code> bit is critical if your agent serves multiple end-users — see our <a href="/blog/multi-tenant-mcp-server">multi-tenant memory post</a> for the design rationale.</p>

<h2>What the agent looks like with memory</h2>

<p>Run the agent. Send a message. Watch the trace — your agent now calls <code>/v1/profile</code> on the first turn (instant personalization), <code>/v1/search/all</code> when the user asks something specific ("what did we decide about the migration last week?"), and <code>/v1/add</code> at the end to persist the new conversation.</p>

<p>Next session, same user, same agent: it remembers. Without you writing any storage code, hosting any database, or maintaining any vector index. The whole memory layer lives behind the OpenAPI import.</p>

<h2>Custom GPTs &amp; Assistants API</h2>

<p>The same OpenAPI URL works in:</p>

<ul>
<li><strong>Custom GPTs</strong> (chatgpt.com/g/...): <em>Configure → Actions → Create new action → Import from URL</em>. Paste the URL, set Bearer auth, ship it. The GPT gains memory across all conversations with each user.</li>
<li><strong>Assistants API</strong> (programmatic): generate function-tool definitions from the OpenAPI spec using <a href="https://platform.openai.com/docs/assistants/tools/function-calling">function calling</a>, attach to your assistant. Works the same way as Agent Builder under the hood.</li>
<li><strong>Any LLM that supports OpenAPI tool import</strong> (Anthropic Claude with tool use, Google Gemini, Mistral) — the spec is provider-agnostic.</li>
</ul>

<h2>Authenticated public spec</h2>

<p>One nuance worth flagging: <code>https://mengram.io/openapi.json</code> is public — it lists every endpoint, including admin and signup ones. Your agent doesn't need most of those, and you don't want to waste tool slots on them. When importing, pick only the endpoints you actually use. OpenAI's tool selection UI lets you uncheck the rest.</p>

<p>If you want a curated subset (e.g. just <code>/v1/add</code> + <code>/v1/search/all</code> + <code>/v1/profile</code>), let us know — we can publish a slim spec at <code>/openapi-agent.json</code> tailored for agent builders. Email <a href="mailto:ali@mengram.io">ali@mengram.io</a>.</p>

<h2>Why this matters</h2>

<p>OpenAI's marketing pitches Agent Builder as a complete agent platform. It's not — it's the prototyping and orchestration layer. Memory, multi-user state, and long-term continuity have to come from somewhere else. Most builders end up writing their own RAG layer, hosting their own Pinecone, and maintaining their own ingestion pipeline. That's months of infrastructure work for a feature their users won't care about until it's missing.</p>

<p>Importing an OpenAPI spec replaces that with a single URL. Your agent gets persistent memory in two minutes. You ship in days, not months.</p>

<p>Try it on your next prototype. If you build something interesting on top of Mengram + Agent Builder, drop a note in our <a href="https://github.com/alibaizhanov/mengram/discussions">discussions</a> — we showcase community integrations.</p>
""",
        "related": ["multi-tenant-mcp-server", "claude-managed-agents-memory", "how-to-add-memory-to-ai-agents"],
    },
    "ai-agent-memory-patterns": {
        "slug": "ai-agent-memory-patterns",
        "title": "5 Patterns We See in Production AI Agent Memory (And How to Build Them)",
        "date": "May 6, 2026",
        "date_iso": "2026-05-06",
        "read_time": "10",
        "tags": ["Patterns", "Architecture", "Use Cases"],
        "excerpt": "Most AI memory tutorials stop at \"add fact, retrieve fact.\" Real production agents use memory in shapes that don't show up in the docs — Daily Briefs that run on cron, multi-tenant SaaS that scope per end-user, knowledge work that has nothing to do with code. Here are five patterns we see across live agents, with the architecture for each.",
        "seo_title": "5 Patterns in Production AI Agent Memory | Mengram",
        "seo_description": "Real shapes of AI agent memory in production: cron-driven Daily Briefs, multi-tenant SaaS, non-developer knowledge work, cloud infra automation, personal dashboards. Architecture and code for each.",
        "seo_keywords": "AI agent memory patterns, production AI memory, agent design patterns, persistent memory agents, daily brief AI agent, multi-tenant agent memory, cron AI agent, AI memory architecture",
        "content_html": """
<h2>Why patterns, not features</h2>

<p>If you read AI memory documentation — ours, Mem0's, Letta's, Zep's — it reads like a SDK manual: <code>add()</code>, <code>search()</code>, <code>get_profile()</code>. Useful, but it doesn't tell you what people actually build with these primitives.</p>

<p>Operating Mengram for the past year has given us a vantage point that no documentation can: we see the <em>shapes</em> agents take in production. The same primitives compose into wildly different products. Some are obvious (a chatbot that remembers your name). Some are not (an autonomous workflow that runs every morning at 10 AM, checks an external file, and acts only if conditions changed since yesterday).</p>

<p>This post catalogs five patterns we keep seeing. They overlap, they evolve, and most production agents combine two or three. If you're starting an agent project, picking the right pattern from day one will save you from rebuilding storage architecture in month four.</p>

<h2>Pattern 1: The Daily Brief</h2>

<p><strong>What it looks like:</strong> An agent that runs on a schedule (cron, GitHub Actions, a hosted scheduler), pulls fresh information from external sources, compares against memory, and emits a digest only if something changed. Common variants: morning news brief, daily KPI report, dependency update summary, security alert digest.</p>

<p><strong>Why memory matters:</strong> Without persistence, every run starts blind. The agent re-summarizes the same article you saw yesterday. It re-reports the same alert. It can't say "this is new since last time" because it has no last time.</p>

<p><strong>Architecture:</strong></p>

<pre><code>cron → fetch sources → search memory ("what did I report yesterday?")
     → diff vs memory → if delta > threshold: emit brief → save brief to memory</code></pre>

<p>The <code>search memory</code> step is where Mengram earns its keep. You're not searching documents — you're searching <em>your own past output</em>. Episodic memory is the natural fit:</p>

<pre><code><span class="c-kw">from</span> <span class="c-fn">mengram</span> <span class="c-kw">import</span> Mengram

m = Mengram(api_key=<span class="c-str">"om-..."</span>)

<span class="c-cmt"># Run at 10:00 AM</span>
yesterday = m.search_all(<span class="c-str">"morning brief topics covered yesterday"</span>, top_k=10)
fresh_topics = fetch_news()
new_only = [t <span class="c-kw">for</span> t <span class="c-kw">in</span> fresh_topics <span class="c-kw">if not</span> any(t.id <span class="c-kw">in</span> r.memory <span class="c-kw">for</span> r <span class="c-kw">in</span> yesterday.episodes)]

<span class="c-kw">if</span> new_only:
    brief = generate_brief(new_only)
    send_email(brief)
    m.add([{{<span class="c-str">"role"</span>: <span class="c-str">"user"</span>, <span class="c-str">"content"</span>: <span class="c-str">f"Brief covered: {{[t.id for t in new_only]}}"</span>}}])</code></pre>

<p>The agent's value compounds with use. By month three, the brief deduplicates against three months of past coverage automatically.</p>

<h2>Pattern 2: Multi-Tenant SaaS Memory</h2>

<p><strong>What it looks like:</strong> A product where each end-user has their own memory scope, but the application itself uses a single Mengram API key. Examples: customer support copilots, AI tutors, sales assistants, personalized coaches.</p>

<p><strong>Why memory matters:</strong> Without per-user isolation, Alice's conversation history bleeds into Bob's. Search returns the wrong context. The agent calls Alice "Bob" because Bob's name appears in higher-frequency memory. Trust collapses.</p>

<p><strong>Architecture:</strong> pass <code>user_id</code> on every memory operation. One API key, infinite isolated memory scopes:</p>

<pre><code><span class="c-cmt"># In your request handler, derive user_id from auth — never from LLM</span>
<span class="c-kw">def</span> handle_message(end_user_id, message):
    profile = m.profile(user_id=end_user_id)
    history = m.search_all(message, user_id=end_user_id, top_k=5)
    response = llm.chat([
        {{<span class="c-str">"role"</span>: <span class="c-str">"system"</span>, <span class="c-str">"content"</span>: profile}},
        *[{{<span class="c-str">"role"</span>: <span class="c-str">"system"</span>, <span class="c-str">"content"</span>: r.memory}} <span class="c-kw">for</span> r <span class="c-kw">in</span> history],
        {{<span class="c-str">"role"</span>: <span class="c-str">"user"</span>, <span class="c-str">"content"</span>: message}},
    ])
    m.add([
        {{<span class="c-str">"role"</span>: <span class="c-str">"user"</span>, <span class="c-str">"content"</span>: message}},
        {{<span class="c-str">"role"</span>: <span class="c-str">"assistant"</span>, <span class="c-str">"content"</span>: response}},
    ], user_id=end_user_id)
    <span class="c-kw">return</span> response</code></pre>

<p>The deep design rationale lives in our <a href="/blog/multi-tenant-mcp-server">multi-tenant MCP server</a> post. The takeaway: never let the LLM choose <code>user_id</code> — always derive it from your auth layer. Anything else is a data leak waiting to happen.</p>

<h2>Pattern 3: Non-Developer Knowledge Work</h2>

<p><strong>What it looks like:</strong> A workflow that has nothing to do with code: drafting briefs, reviewing documents for sensitive language, cross-referencing meeting notes, organizing a coalition's working groups. The user is a researcher, organizer, lawyer, journalist — not an engineer.</p>

<p><strong>Why memory matters:</strong> Knowledge work is fundamentally about <em>connecting current input to remembered prior context</em>. "We discussed this in the meeting two weeks ago" is the operative phrase. Without persistence, the AI is reduced to a souped-up Ctrl+F.</p>

<p><strong>Architecture:</strong> the agent here is usually a Claude Desktop / Cursor / Custom GPT setup with Mengram as MCP server. The user types in natural language, the agent recalls past context, drafts revisions, flags inconsistencies. No custom code:</p>

<pre><code>{{
  <span class="c-str">"mcpServers"</span>: {{
    <span class="c-str">"mengram"</span>: {{
      <span class="c-str">"command"</span>: <span class="c-str">"/path/to/mengram"</span>,
      <span class="c-str">"args"</span>: [<span class="c-str">"server"</span>, <span class="c-str">"--cloud"</span>],
      <span class="c-str">"env"</span>: {{ <span class="c-str">"MENGRAM_API_KEY"</span>: <span class="c-str">"om-..."</span> }}
    }}
  }}
}}</code></pre>

<p>The interesting wrinkle: knowledge workers structure memory differently from developers. Where a developer entity might be <code>"AWS Lambda"</code> with facts about config and limits, a knowledge worker's entity is <code>"Partner Working Group"</code> with facts about who attended, what was decided, and which document captured the outcome. Same memory primitives, vastly different shape.</p>

<p>Procedural memory shows up here too — recurring workflows like "draft a coalition brief, route to legal review, scrub for leak-risk language." The procedure evolves over time as the workflow tightens.</p>

<h2>Pattern 4: Cloud Infrastructure Automation</h2>

<p><strong>What it looks like:</strong> An agent that manages a sprawl of cloud resources — AWS roles, DNS records, certificates, billing alerts, deployment pipelines. The user describes what they want in natural language; the agent recalls the existing state, calls the right APIs, and updates memory with the change.</p>

<p><strong>Why memory matters:</strong> Cloud accounts accumulate state at a rate humans cannot track. By month two there are 80+ IAM roles, 200+ DNS records, dozens of certificates. Without memory, every change is a fresh archaeology dig.</p>

<p><strong>Architecture:</strong> entities representing cloud resources, with facts updated on every <code>describe-*</code> API call. Procedures capturing repeatable workflows ("monthly billing report upload," "rotate IAM keys").</p>

<pre><code><span class="c-cmt"># When user asks "what AWS Lambda functions do we have?"</span>
results = m.search_all(<span class="c-str">"AWS Lambda functions"</span>, type_filter=<span class="c-str">"technology"</span>)

<span class="c-cmt"># When user asks "rotate keys for staging" — recall the procedure</span>
procedures = m.search_all(<span class="c-str">"rotate keys staging"</span>, type=<span class="c-str">"procedural"</span>)
<span class="c-cmt"># Agent now knows the exact 6-step workflow it ran last time</span></code></pre>

<p>Procedural memory is the load-bearing piece here. Every successful infra workflow gets captured as a procedure with steps. When a step fails next time, the procedure auto-evolves. The agent doesn't just know <em>what to do</em> — it knows <em>what worked last time and what didn't</em>.</p>

<h2>Pattern 5: Personal Life Dashboard</h2>

<p><strong>What it looks like:</strong> An AI assistant that knows your routines, relationships, projects, preferences — and uses that knowledge to surface what matters. Daily check-ins, reminders synthesized from past intent, smart triggers when something contradicts what was recorded.</p>

<p><strong>Why memory matters:</strong> This is the original "personal AI" promise. Without long-term memory it's a chatbot that forgets your spouse's name between sessions.</p>

<p><strong>Architecture:</strong> entities for people in your life, your projects, your devices, your preferences. Episodes for events. Cognitive Profile for instant personalization on every request:</p>

<pre><code>profile = m.profile()
<span class="c-cmt"># Returns a system-prompt-ready summary like:</span>
<span class="c-cmt"># "User is a backend engineer at Stripe, lives in SF, has a partner Sarah and a cat Mochi.</span>
<span class="c-cmt">#  Working on the migration from Airflow to Prefect 3 (deadline May 20). Mood lately:</span>
<span class="c-cmt">#  productive but anxious about the move."</span>

response = llm.chat([
    {{<span class="c-str">"role"</span>: <span class="c-str">"system"</span>, <span class="c-str">"content"</span>: profile}},
    *messages,
])</code></pre>

<p>The trap with this pattern is over-collection. Memory grows fast — a few weeks in, search results dilute with irrelevant history. The fix is decay (Mengram weights memories with Ebbinghaus decay) plus periodic curator passes that consolidate or archive stale facts.</p>

<h2>How patterns combine</h2>

<p>Real production agents are usually two or three patterns stacked:</p>

<ul>
<li>A <strong>Daily Brief + Personal Life Dashboard</strong> — your morning agent that already knows what you care about</li>
<li>A <strong>Multi-Tenant SaaS + Cloud Infra Automation</strong> — an internal tool where each engineer has their own memory of the AWS resources they own</li>
<li>A <strong>Non-Developer Knowledge Work + Multi-Tenant SaaS</strong> — a coalition platform where each working group has its own scoped memory</li>
</ul>

<p>The mistake we see most often: starting with the wrong primary pattern. Builders start with "I'll add memory to my chatbot" (a chatbot pattern), but what they actually need is the Daily Brief pattern — where memory is the diff against past output, not the conversation history.</p>

<p>Pick the pattern that matches your <em>workflow shape</em>, not your <em>interface shape</em>.</p>

<h2>What to do next</h2>

<p>If one of these patterns matched your project, the architecture above maps directly onto Mengram's primitives. The <a href="/blog/semantic-episodic-procedural-memory">three memory types</a> cover every shape we've shown:</p>

<ul>
<li>Semantic (entities + facts) — Patterns 2, 3, 5</li>
<li>Episodic (events + outcomes) — Patterns 1, 5</li>
<li>Procedural (workflows that evolve) — Patterns 1, 4</li>
</ul>

<p>If you're not sure which pattern fits, the simplest test is: <em>what does your agent need to remember between sessions?</em> If you can't answer in one sentence, you're probably trying to combine too many patterns at once. Start with one. Memory is composable — you can always add another layer.</p>

<p>And if you're building something that doesn't fit any of these, we want to hear about it — open a discussion on <a href="https://github.com/alibaizhanov/mengram/discussions">our repo</a>. The pattern catalog grows from real builds.</p>
""",
        "related": ["semantic-episodic-procedural-memory", "multi-tenant-mcp-server", "how-to-add-memory-to-ai-agents"],
    },
}
