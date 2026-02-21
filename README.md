<div align="center">

# Mengram

### The memory layer for AI agents that learns from experience

Your agents remember facts, events, and workflows — and **procedures improve automatically when they fail.**

[![PyPI](https://img.shields.io/pypi/v/mengram-ai)](https://pypi.org/project/mengram-ai/)
[![npm](https://img.shields.io/npm/v/mengram-ai)](https://www.npmjs.com/package/mengram-ai)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![PyPI Downloads](https://img.shields.io/pypi/dm/mengram-ai)](https://pypi.org/project/mengram-ai/)

**[Website](https://mengram.io)** · **[Get API Key](https://mengram.io/dashboard)** · **[API Docs](https://mengram.io/docs)** · **[Examples](examples/)**

</div>

---

## Why Mengram?

Every AI memory tool stores facts. Mengram stores **3 types** — and procedures **evolve from failures**.

|  | Mengram | Mem0 | Letta | Zep |
|---|---|---|---|---|
| Semantic Memory (facts) | ✅ | ✅ | ✅ | ✅ |
| **Episodic Memory (events)** | ✅ | ❌ | Partial | ❌ |
| **Procedural Memory (workflows)** | ✅ | ❌ | ❌ | ❌ |
| **Experience-Driven Evolution** | ✅ | ❌ | ❌ | ❌ |
| **Cognitive Profile** | ✅ | ❌ | ❌ | ❌ |
| **Multi-User Isolation** | ✅ | ✅ | ❌ | ✅ |
| Knowledge Graph | ✅ | ✅ | ✅ | ✅ |
| LangChain / CrewAI / OpenClaw | ✅ | Partial | ❌ | ✅ |
| **Import (ChatGPT, Obsidian)** | ✅ | ❌ | ❌ | ❌ |
| MCP Server | ✅ | ✅ | ✅ | ❌ |
| **Price** | **Free** | $19–249/mo | Free (self-host) | Enterprise |

## Quick Start

```bash
pip install mengram-ai
```

```python
from cloud.client import CloudMemory

m = CloudMemory(api_key="om-...")  # Free key → mengram.io/dashboard

# Add a conversation — Mengram auto-extracts facts, events, and workflows
m.add([
    {"role": "user", "content": "Deployed to Railway today. Build passed but forgot migrations — DB crashed. Fixed by adding a pre-deploy check."},
])

# Search facts
m.search("deployment setup")

# Search events — what happened?
m.episodes(query="deployment")
# → [{summary: "Deployed to Railway, DB crashed due to missing migrations", outcome: "resolved", ...}]

# Search workflows — how to do it?
m.procedures(query="deploy")
# → [{name: "Deploy to Railway", steps: ["build", "run migrations", "push", "verify"], ...}]

# Unified search — all 3 types at once
m.search_all("deployment issues")
# → {semantic: [...], episodic: [...], procedural: [...]}
```

**JavaScript / TypeScript:**
```bash
npm install mengram-ai
```
```javascript
const { MengramClient } = require('mengram-ai');
const m = new MengramClient('om-...');

await m.add([{ role: 'user', content: 'Fixed OOM with Redis cache' }]);
const all = await m.searchAll('database issues');
// → { semantic: [...], episodic: [...], procedural: [...] }
```

## Experience-Driven Procedures

**The feature no one else has.** Procedures learn from real outcomes — not static runbooks.

```
Week 1:  "Deploy" → build → push → deploy
                                         ↓ FAILURE: forgot migrations, DB crashed
Week 2:  "Deploy" v2 → build → run migrations → push → deploy
                                                          ↓ FAILURE: OOM on Railway
Week 3:  "Deploy" v3 → build → run migrations → check memory → push → deploy ✅
```

This happens **automatically** when you report failures:

```python
# Report failure with context → procedure evolves to a new version
m.procedure_feedback(proc_id, success=False,
                     context="OOM error on step 3", failed_at_step=3)

# View version history
history = m.procedure_history(proc_id)
# → {versions: [v1, v2, v3], evolution_log: [{change: "step_added", reason: "prevent OOM"}]}
```

Or **fully automatic** — add conversations and Mengram detects failures, links them to procedures, and evolves:

```python
m.add([{"role": "user", "content": "Deploy to Railway failed again — OOM on the build step"}])
# → Episode auto-linked to "Deploy" procedure → failure detected → v3 created
```

## Cognitive Profile

One API call generates a system prompt from all your memories:

```python
profile = m.get_profile()
# → "You are talking to Ali, a developer in Almaty building Mengram.
#    He uses Python, PostgreSQL, and Railway. Recently debugged pgvector deployment.
#    Workflows: deploys via build→twine→npm→git. Communicate directly, focus on practical next steps."
```

Insert into any LLM's system prompt for instant personalization.

## Import Existing Data

Kill the cold-start problem — import your ChatGPT history, Obsidian vault, or text files:

```bash
# ChatGPT export (Settings → Data Controls → Export)
mengram import chatgpt ~/Downloads/chatgpt-export.zip --cloud

# Obsidian vault
mengram import obsidian ~/Documents/MyVault --cloud

# Any text/markdown files
mengram import files notes/*.md --cloud
```

Works with Python SDK too:
```python
m = CloudMemory(api_key="om-...")
m.import_chatgpt("export.zip")
m.import_obsidian("~/Documents/MyVault")
m.import_files(["notes.md", "journal.txt"])
```

## Integrations

### MCP Server (Claude Desktop, Cursor, Windsurf)

```json
{
  "mcpServers": {
    "mengram": {
      "command": "mengram",
      "args": ["server", "--cloud"],
      "env": { "MENGRAM_API_KEY": "om-..." }
    }
  }
}
```

### LangChain

```python
from integrations.langchain import MengramChatMessageHistory, MengramRetriever

# Drop-in message history — auto-saves to Mengram
history = MengramChatMessageHistory(api_key="om-...", session_id="session-1")

# RAG retriever — searches all 3 memory types
retriever = MengramRetriever(api_key="om-...")
```

### CrewAI

```python
from integrations.crewai import create_mengram_tools

tools = create_mengram_tools(api_key="om-...")
# → 5 tools: search, remember, profile, save_workflow, workflow_feedback

agent = Agent(role="Support Engineer", tools=tools)
```

### OpenClaw

```bash
openclaw plugins install openclaw-mengram
```

```json
{
  "plugins": {
    "entries": {
      "openclaw-mengram": {
        "enabled": true,
        "config": { "apiKey": "${MENGRAM_API_KEY}" }
      }
    },
    "slots": { "memory": "openclaw-mengram" }
  }
}
```

Auto-recall before every turn, auto-capture after every turn. 6 tools, slash commands, CLI. [GitHub](https://github.com/alibaizhanov/openclaw-mengram) · [npm](https://www.npmjs.com/package/openclaw-mengram)

## Agent Templates

Ready-to-run examples — clone, set API key, run in 5 minutes:

| Template | Stack | What it shows |
|---|---|---|
| **[DevOps Agent](examples/devops-agent/)** | Python SDK | Procedures that evolve from deployment failures |
| **[Customer Support](examples/customer-support-agent/)** | CrewAI | Agent with 5 memory tools, remembers returning customers |
| **[Personal Assistant](examples/personal-assistant/)** | LangChain | Cognitive profile + auto-saving chat history |

```bash
cd examples/devops-agent && pip install -r requirements.txt
export MENGRAM_API_KEY=om-...
python main.py
```

## API Reference

All endpoints require `Authorization: Bearer om-...`. Your API key identifies the account. Pass `user_id` to isolate data per end-user (multi-tenant apps).

| Endpoint | Description |
|---|---|
| `POST /v1/add` | Add memories (auto-extracts all 3 types) |
| `POST /v1/search` | Semantic search |
| `POST /v1/search/all` | Unified search (all 3 types) |
| `GET /v1/episodes/search` | Search episodic memories |
| `GET /v1/procedures/search` | Search procedural memories |
| `PATCH /v1/procedures/{id}/feedback` | Report success/failure → triggers evolution |
| `GET /v1/procedures/{id}/history` | Version history + evolution log |
| `GET /v1/profile` | Cognitive Profile |
| `GET /v1/triggers` | Smart Triggers (reminders, contradictions, patterns) |
| `POST /v1/agents/run` | Run memory agents (Curator, Connector, Digest) |

### Multi-User Isolation

Building an app with multiple users? Pass `user_id` to isolate memories per end-user. One API key, many users — each sees only their own data:

```python
# Each user_id gets its own isolated memory space
m.add([...], user_id="alice")
m.add([...], user_id="bob")

m.search_all("preferences", user_id="alice")  # Only Alice's memories
m.search_all("preferences", user_id="bob")    # Only Bob's memories

m.get_profile(user_id="alice")  # Alice's cognitive profile
```

```javascript
await m.add([...], { userId: 'alice' });
await m.searchAll('preferences', { userId: 'alice' });  // Only Alice's memories
```

No `user_id`? Everything works as before — defaults to a single shared memory space.

Full interactive docs: **[mengram.io/docs](https://mengram.io/docs)**

## License

Apache 2.0 — free for commercial use.

---

<div align="center">

**Built by [Ali Baizhanov](https://github.com/alibaizhanov)** · **[mengram.io](https://mengram.io)**

</div>
