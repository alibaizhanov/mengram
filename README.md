<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/Mengram-a855f7?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMjAgMTIwIj48cGF0aCBkPSJNNjAgMTYgUTkyIDE2IDk2IDQ4IFExMDAgNzggNzIgODggUTUwIDk2IDM4IDc2IFEyNiA1OCA0NiA0NiBRNjIgMzggNzAgNTIgUTc2IDY0IDYyIDY4IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmYiIHN0cm9rZS13aWR0aD0iOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+PGNpcmNsZSBjeD0iNjIiIGN5PSI2OCIgcj0iOCIgZmlsbD0iI2ZmZiIvPjwvc3ZnPg==">
  <img alt="Mengram" src="https://img.shields.io/badge/Mengram-a855f7?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMjAgMTIwIj48cGF0aCBkPSJNNjAgMTYgUTkyIDE2IDk2IDQ4IFExMDAgNzggNzIgODggUTUwIDk2IDM4IDc2IFEyNiA1OCA0NiA0NiBRNjIgMzggNzAgNTIgUTc2IDY0IDYyIDY4IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmYiIHN0cm9rZS13aWR0aD0iOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+PGNpcmNsZSBjeD0iNjIiIGN5PSI2OCIgcj0iOCIgZmlsbD0iI2ZmZiIvPjwvc3ZnPg==">
</picture>

### Give your AI agents memory that actually learns

[![PyPI](https://img.shields.io/pypi/v/mengram-ai)](https://pypi.org/project/mengram-ai/)
[![npm](https://img.shields.io/npm/v/mengram-ai)](https://www.npmjs.com/package/mengram-ai)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![PyPI Downloads](https://img.shields.io/pypi/dm/mengram-ai)](https://pypi.org/project/mengram-ai/)

**[Website](https://mengram.io)** · **[Get API Key](https://mengram.io/#signup)** · **[Docs](https://mengram.io/docs)** · **[Console](https://mengram.io/dashboard)** · **[Examples](examples/)**

</div>

```bash
pip install mengram-ai   # or: npm install mengram-ai
```

```python
from mengram import Mengram
m = Mengram(api_key="om-...")           # Free key → mengram.io

m.add([{"role": "user", "content": "I use Python and deploy to Railway"}])
m.search("tech stack")                  # → facts
m.episodes(query="deployment")          # → events
m.procedures(query="deploy")            # → workflows that evolve from failures
```

---

## Why Mengram?

Every AI memory tool stores facts. Mengram stores **3 types of memory** — and procedures **evolve when they fail**.

|  | Mengram | Mem0 | Zep | Letta |
|---|:---:|:---:|:---:|:---:|
| Semantic memory (facts, preferences) | **Yes** | Yes | Yes | Yes |
| **Episodic memory (events, decisions)** | **Yes** | No | No | Partial |
| **Procedural memory (workflows)** | **Yes** | No | No | No |
| **Procedures evolve from failures** | **Yes** | No | No | No |
| **Cognitive Profile** | **Yes** | No | No | No |
| Multi-user isolation | **Yes** | Yes | Yes | No |
| Knowledge graph | **Yes** | Yes | Yes | Yes |
| LangChain + CrewAI + MCP | **Yes** | Partial | Partial | Partial |
| **Import ChatGPT / Obsidian** | **Yes** | No | No | No |
| Pricing | **Free tier** | $19-249/mo | Enterprise | Self-host |

## Get Started in 30 Seconds

**1. Get a free API key** at [mengram.io](https://mengram.io/#signup) (email or GitHub)

**2. Install**

```bash
pip install mengram-ai
```

**3. Use**

```python
from mengram import Mengram

m = Mengram(api_key="om-...")

# Add a conversation — auto-extracts facts, events, and workflows
m.add([
    {"role": "user", "content": "Deployed to Railway today. Build passed but forgot migrations — DB crashed. Fixed by adding a pre-deploy check."},
])

# Search across all 3 memory types at once
results = m.search_all("deployment issues")
# → {semantic: [...], episodic: [...], procedural: [...]}
```

<details>
<summary><b>JavaScript / TypeScript</b></summary>

```bash
npm install mengram-ai
```

```javascript
const { MengramClient } = require('mengram-ai');
const m = new MengramClient('om-...');

await m.add([{ role: 'user', content: 'Fixed OOM by adding Redis cache layer' }]);
const results = await m.searchAll('database issues');
// → { semantic: [...], episodic: [...], procedural: [...] }
```

</details>

<details>
<summary><b>REST API (curl)</b></summary>

```bash
# Add memory
curl -X POST https://mengram.io/v1/add \
  -H "Authorization: Bearer om-..." \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "I prefer dark mode and vim keybindings"}]}'

# Search all 3 types
curl -X POST https://mengram.io/v1/search/all \
  -H "Authorization: Bearer om-..." \
  -d '{"query": "user preferences"}'
```

</details>

## 3 Memory Types

### Semantic — facts, preferences, knowledge

```python
m.search("tech stack")
# → ["Uses Python 3.12", "Deploys to Railway", "PostgreSQL with pgvector"]
```

### Episodic — events, decisions, outcomes

```python
m.episodes(query="deployment")
# → [{summary: "DB crashed due to missing migrations", outcome: "resolved", date: "2025-05-12"}]
```

### Procedural — workflows that evolve

```
Week 1:  "Deploy" → build → push → deploy
                                         ↓ FAILURE: forgot migrations
Week 2:  "Deploy" v2 → build → run migrations → push → deploy
                                                          ↓ FAILURE: OOM
Week 3:  "Deploy" v3 → build → run migrations → check memory → push → deploy ✅
```

This happens **automatically** when you report failures:

```python
m.procedure_feedback(proc_id, success=False,
                     context="OOM error on step 3", failed_at_step=3)
# → Procedure evolves to v3 with new step added
```

Or **fully automatic** — just add conversations and Mengram detects failures and evolves procedures:

```python
m.add([{"role": "user", "content": "Deploy failed again — OOM on the build step"}])
# → Episode created → linked to "Deploy" procedure → failure detected → v3 created
```

## Cognitive Profile

One API call generates a system prompt from all memories:

```python
profile = m.get_profile()
# → "You are talking to Ali, a developer in Almaty. Uses Python, PostgreSQL,
#    and Railway. Recently debugged pgvector deployment. Prefers direct
#    communication and practical next steps."
```

Insert into any LLM's system prompt for instant personalization.

## Import Existing Data

Kill the cold-start problem:

```bash
mengram import chatgpt ~/Downloads/chatgpt-export.zip --cloud   # ChatGPT history
mengram import obsidian ~/Documents/MyVault --cloud              # Obsidian vault
mengram import files notes/*.md --cloud                          # Any text/markdown
```

## Integrations

<table>
<tr>
<td width="50%">

**MCP Server** — Claude Desktop, Cursor, Windsurf

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

26 tools for memory management.

</td>
<td width="50%">

**LangChain** — `pip install langchain-mengram`

```python
from langchain_mengram import (
    MengramRetriever,
    MengramChatMessageHistory,
)

retriever = MengramRetriever(api_key="om-...")
docs = retriever.invoke("deployment issues")
```

</td>
</tr>
<tr>
<td>

**CrewAI**

```python
from integrations.crewai import create_mengram_tools

tools = create_mengram_tools(api_key="om-...")
# → 5 tools: search, remember, profile,
#   save_workflow, workflow_feedback

agent = Agent(role="Support", tools=tools)
```

</td>
<td>

**OpenClaw**

```bash
openclaw plugins install openclaw-mengram
```

Auto-recall before every turn, auto-capture after. 12 tools, slash commands, Graph RAG.

[GitHub](https://github.com/alibaizhanov/openclaw-mengram) · [npm](https://www.npmjs.com/package/openclaw-mengram)

</td>
</tr>
</table>

## Multi-User Isolation

One API key, many users — each sees only their own data:

```python
m.add([...], user_id="alice")
m.add([...], user_id="bob")

m.search_all("preferences", user_id="alice")  # Only Alice's memories
m.get_profile(user_id="alice")                 # Alice's cognitive profile
```

## Async Client

Non-blocking Python client built on httpx:

```python
from mengram import AsyncMengram

async with AsyncMengram() as m:
    await m.add([{"role": "user", "content": "I use async/await"}])
    results = await m.search("async")
    profile = await m.get_profile()
```

Install with `pip install mengram-ai[async]`.

## Metadata Filters

Filter search results by metadata:

```python
results = m.search("config", filters={"agent_id": "support-bot", "app_id": "prod"})
```

## Webhooks

Get notified when memories change:

```python
m.create_webhook(
    url="https://your-app.com/hook",
    event_types=["memory_add", "memory_update"],
)
```

## Agent Templates

Clone, set API key, run in 5 minutes:

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

| Endpoint | Description |
|---|---|
| `POST /v1/add` | Add memories (auto-extracts all 3 types) |
| `POST /v1/search` | Semantic search |
| `POST /v1/search/all` | Unified search (semantic + episodic + procedural) |
| `GET /v1/episodes/search` | Search events and decisions |
| `GET /v1/procedures/search` | Search workflows |
| `PATCH /v1/procedures/{id}/feedback` | Report outcome — triggers evolution |
| `GET /v1/procedures/{id}/history` | Version history + evolution log |
| `GET /v1/profile` | Cognitive Profile |
| `GET /v1/triggers` | Smart Triggers (reminders, contradictions, patterns) |
| `POST /v1/agents/run` | Memory agents (Curator, Connector, Digest) |
| `GET /v1/me` | Account info |

Full interactive docs: **[mengram.io/docs](https://mengram.io/docs)**

## Community

- **[GitHub Issues](https://github.com/alibaizhanov/mengram/issues)** — bug reports, feature requests
- **[API Docs](https://mengram.io/docs)** — interactive Swagger UI
- **[Examples](examples/)** — ready-to-run agent templates

## License

Apache 2.0 — free for commercial use.

---

<div align="center">

**[Get your free API key](https://mengram.io/#signup)** · Built by **[Ali Baizhanov](https://github.com/alibaizhanov)** · **[mengram.io](https://mengram.io)**

</div>
