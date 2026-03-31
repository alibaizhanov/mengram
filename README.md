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

## Claude Code — Zero-Config Memory

Two commands. Claude Code remembers everything across sessions automatically.

```bash
pip install mengram-ai
mengram setup              # Sign up + install hooks (interactive)
```

Or manually: `export MENGRAM_API_KEY=om-...` → `mengram hook install`

What happens:

```
Session Start  →  Loads your cognitive profile (who you are, preferences, tech stack)
Every Prompt   →  Searches past sessions for relevant context (auto-recall)
After Response →  Saves new knowledge in background (auto-save)
```

No manual saves. No tool calls. Claude just knows what you worked on yesterday.

```bash
mengram hook status     # check what's installed
mengram hook uninstall  # remove all hooks
```

---

## Why Mengram?

Every AI memory tool stores facts. Mengram stores **3 types of memory** — and procedures **evolve when they fail**.

|  | Mengram | claude-mem | Mem0 | Zep | Letta |
|---|:---:|:---:|:---:|:---:|:---:|
| Semantic memory (facts, preferences) | **Yes** | Yes | Yes | Yes | Yes |
| **Episodic memory (events, decisions)** | **Yes** | Partial | No | No | Partial |
| **Procedural memory (workflows)** | **Yes** | No | No | No | No |
| **Procedures evolve from failures** | **Yes** | No | No | No | No |
| **Cognitive Profile** | **Yes** | No | No | No | No |
| Multi-user isolation | **Yes** | No | Yes | Yes | No |
| Knowledge graph | **Yes** | No | Yes | Yes | Yes |
| Claude Code hooks (auto-save/recall) | **Yes** | **Yes** | No | No | No |
| LangChain + CrewAI + MCP | **Yes** | No | Partial | Partial | Partial |
| **Import ChatGPT / Obsidian** | **Yes** | No | No | No | No |
| Pricing | **Free tier** | Free / OSS | $19-249/mo | Enterprise | Self-host |

## Get Started in 30 Seconds

**1. Install**

```bash
pip install mengram-ai
```

**2. Setup** (creates account + installs Claude Code hooks)

```bash
mengram setup
```

Or get a key manually at [mengram.io](https://mengram.io/#signup) and `export MENGRAM_API_KEY=om-...`

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
<summary><b>File Upload (PDF, DOCX, TXT, MD)</b></summary>

```python
# Upload a PDF — auto-extracts memories using vision AI
result = m.add_file("meeting-notes.pdf")
# → {"status": "accepted", "job_id": "job-...", "page_count": 12}

# Poll for completion
m.job_status(result["job_id"])
```

```javascript
// Node.js — pass a file path
await m.addFile('./report.pdf');

// Browser — pass a File object from <input type="file">
await m.addFile(fileInput.files[0]);
```

```bash
# REST API
curl -X POST https://mengram.io/v1/add_file \
  -H "Authorization: Bearer om-..." \
  -F "file=@meeting-notes.pdf" \
  -F "user_id=default"
```

</details>

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

**Claude Code** — Auto-memory hooks

```bash
mengram hook install
```

3 hooks: profile on start, recall on every prompt, save after responses. Zero manual effort.

[Docs](https://mengram.io/docs/claude-code)

</td>
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

29 tools for memory management.

</td>
</tr>
<tr>
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
<td width="50%">

**CrewAI**

```python
from integrations.crewai import create_mengram_tools

tools = create_mengram_tools(api_key="om-...")
# → 5 tools: search, remember, profile,
#   save_workflow, workflow_feedback

agent = Agent(role="Support", tools=tools)
```

</td>
</tr>
<tr>
<td width="50%">

**OpenClaw**

```bash
openclaw plugins install openclaw-mengram
```

Auto-recall before every turn, auto-capture after. 12 tools, slash commands, Graph RAG.

[GitHub](https://github.com/alibaizhanov/openclaw-mengram) · [npm](https://www.npmjs.com/package/openclaw-mengram)

</td>
<td width="50%">

**CLI** — Full command-line interface

```bash
mengram search "deployment" --cloud
mengram profile --cloud
mengram import chatgpt export.zip --cloud
mengram hook install
```

[Docs](https://mengram.io/docs/cli)

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

## Use with AI Agents

Mengram works as a persistent memory backend for autonomous agents. Your agent stores what it learns, and recalls it on the next run — getting smarter over time.

```python
from mengram import Mengram

m = Mengram(api_key="om-...")

# Agent completes a task → store what happened
m.add([
    {"role": "user", "content": "Apply to Acme Corp on Greenhouse"},
    {"role": "assistant", "content": "Applied successfully. Had to use React Select workaround for dropdowns."},
])
# → Extracts: fact ("applied to Acme Corp"), episode ("Greenhouse application"),
#   procedure ("React Select dropdown workaround")

# Next run → agent recalls what worked before
context = m.search_all("Greenhouse application tips")
# → Returns past procedures, failures, and successful strategies

# Report outcome → procedures evolve
m.procedure_feedback(proc_id, success=False,
                     context="Dropdown fix stopped working")
# → Procedure auto-evolves to a new version
```

Works with any agent framework — CrewAI, LangChain, AutoGPT, custom loops. The agent just calls `add()` after actions and `search()` before decisions.

## Self-Hosted (Ollama)

When running locally with Ollama, use models with **8B+ parameters** and **8K+ context window**. The extraction prompt is ~4,000 tokens — smaller models will hallucinate or mix examples with real data.

| Model | Parameters | Works? |
|-------|-----------|--------|
| `llama3.1:8b` | 8B | Yes |
| `mistral:7b` | 7B | Yes |
| `gemma2:9b` | 9B | Yes |
| `llama3.1:70b` | 70B | Best |
| `phi4-mini:3.8b` | 3.8B | No — context too small |

## API Reference

| Endpoint | Description |
|---|---|
| `POST /v1/add` | Add memories (auto-extracts all 3 types) |
| `POST /v1/add_text` | Add memories from plain text |
| `POST /v1/add_file` | Upload file (PDF, DOCX, TXT, MD) — vision AI extraction |
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
