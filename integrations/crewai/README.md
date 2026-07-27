# mengram-crewai

[Mengram](https://mengram.io) memory tools for [CrewAI](https://crewai.com) agents — semantic (facts), episodic (events), and procedural memory (workflows that learn from failures).

The unique part is `get_procedures`: step-by-step playbooks with a success/failure track record and **preconditions** — the assumptions that were violated when the workflow previously failed — so your agents stop repeating mistakes they already made.

## Install

```bash
pip install mengram-crewai
```

Free API key at [mengram.io](https://mengram.io) (40 memory adds + 200 searches/mo, no card).

## Usage

```python
from crewai import Agent
from mengram_crewai import mengram_tools

agent = Agent(
    role="DevOps engineer",
    goal="Ship the release safely",
    tools=mengram_tools(api_key="om-...", user_id="user-123"),
)
```

The agent gets three tools:

| Tool | What it does |
|---|---|
| `search_memory` | Semantic search over facts, preferences, events, decisions |
| `save_memory` | Save a durable fact to long-term memory |
| `get_procedures` | Learned workflows: steps, track record, preconditions to verify |

## Multi-user

Pass your end-user's id as `user_id` — isolated facts, events, workflows, and profile per user under one API key. See [mengram.io/for-agents](https://mengram.io/for-agents).

## Options

```python
mengram_tools(
    api_key="om-...",         # or rely on MENGRAM_API_KEY env var
    user_id="user-123",       # multi-user isolation
    limit=5,                  # max results per tool call
    base_url="https://...",   # self-hosted instances
)
```

Apache 2.0 · [Docs](https://docs.mengram.io) · [GitHub](https://github.com/alibaizhanov/mengram)
