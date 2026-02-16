# 🧠 Mengram — AI Memory Layer with Autonomous Agents

**Open-source memory layer for AI apps.** Not just storage — Mengram has autonomous agents that clean, analyze, and find hidden patterns in your knowledge.

[![PyPI](https://img.shields.io/pypi/v/mengram-ai)](https://pypi.org/project/mengram-ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**[Website](https://mengram.io)** · **[Dashboard](https://mengram.io/dashboard)** · **[API Docs](https://mengram.io/docs)** · **[PyPI](https://pypi.org/project/mengram-ai/)**

---

## Why Mengram?

|  | Mengram | Mem0 |
|---|---|---|
| Memory Storage | ✅ | ✅ |
| Semantic Search | ✅ | ✅ |
| Knowledge Graph | ✅ | ✅ |
| **Autonomous Agents** | ✅ Curator, Connector, Digest | ❌ |
| **Team Shared Memory** | ✅ Invite codes, privacy controls | ❌ |
| **AI Reflections** | ✅ Patterns, insights, behavioral analysis | ❌ |
| Webhooks | ✅ | ✅ |
| MCP Server | ✅ Claude Desktop, Cursor, Windsurf | ❌ |
| Self-hostable | ✅ | ✅ |
| **Price** | **Free** | $19-249/mo |

## Quick Start (60 seconds)

### 1. Get API key
Sign up at [mengram.io](https://mengram.io) — free, no credit card.

### 2. Install
```bash
pip install mengram-ai
```

### 3. Connect to Claude Desktop
Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "mengram": {
      "command": "mengram",
      "args": ["server", "--cloud"],
      "env": {
        "MENGRAM_API_KEY": "your-key-here"
      }
    }
  }
}
```

Done. Claude now has persistent memory.

## Python SDK

```python
from mengram.cloud.client import CloudMemory

m = CloudMemory(api_key="om-...")

# Add memories from conversation
m.add([
    {"role": "user", "content": "I deployed Mengram on Railway with PostgreSQL 15"},
    {"role": "assistant", "content": "Great, noted the deployment setup."}
], user_id="ali")

# Semantic search
results = m.search("deployment setup", user_id="ali")

# Run memory agents
m.run_agents(agent="all", auto_fix=True)

# Get AI insights
insights = m.insights()

# Team memory
team = m.create_team("Backend Team")
m.share_memory("Redis", team_id=team["id"])

# Webhooks
m.create_webhook(url="https://hooks.slack.com/...", name="Slack")
```

## Memory Agents

Three autonomous agents that analyze your memory:

**🧹 Curator** — Finds contradictions, stale facts, duplicates. Auto-cleans with `auto_fix=True`. Reports memory health score.

**🔗 Connector** — Discovers hidden connections, behavioral patterns, skill clusters. Gives strategic suggestions with priorities.

**📰 Digest** — Weekly summary with headlines, trends, focus areas, and recommendations.

```bash
curl -X POST "https://mengram.io/v1/agents/run?agent=all&auto_fix=true" \
  -H "Authorization: Bearer YOUR_KEY"
```

## Team Shared Memory

Share knowledge across your team. Create → invite → share:

```bash
# Create team → get invite code
POST /v1/teams {"name": "Backend Team"}

# Colleague joins with code
POST /v1/teams/join {"invite_code": "xK9m2Qw5ab"}

# Share an entity
POST /v1/teams/3/share {"entity": "Redis"}
```

Search automatically includes shared team knowledge.

## Webhooks

```python
m.create_webhook(
    url="https://webhook.site/your-id",
    event_types=["memory_add", "memory_update", "memory_delete"],
    secret="optional-hmac-secret"
)
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `POST /v1/add` | Add memories from conversation |
| `POST /v1/search` | Semantic search |
| `POST /v1/agents/run` | Run memory agents |
| `GET /v1/insights` | AI-generated insights |
| `POST /v1/teams` | Create team |
| `POST /v1/teams/join` | Join team |
| `POST /v1/webhooks` | Create webhook |
| `GET /v1/graph` | Knowledge graph |
| `GET /v1/timeline` | Temporal search |
| `GET /v1/stats` | Usage statistics |

Full docs: **https://mengram.io/docs**

## Architecture

```
┌──────────────────────────────────────┐
│          Your AI Clients             │
│  Claude Desktop · Cursor · Windsurf  │
└──────────────┬───────────────────────┘
               │ MCP / REST API
┌──────────────▼───────────────────────┐
│        Mengram Cloud API             │
│  Extraction · Re-ranking · Search    │
├──────────────────────────────────────┤
│       Memory Agents Layer            │
│  🧹 Curator · 🔗 Connector · 📰 Digest│
├──────────────────────────────────────┤
│       Storage Layer                  │
│  PostgreSQL · pgvector · Teams       │
│  Webhooks · Reflections · Graph      │
└──────────────────────────────────────┘
```

## License

MIT

---

Built by **[Ali Baizhanov](https://github.com/alibaizhanov)**
