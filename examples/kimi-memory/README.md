# Give your Kimi K3 agent long-term memory

Kimi K3 is a strong agent brain — but like every LLM, it starts each session
amnesiac. It re-derives your project, your preferences, and the mistakes it
already made. This wires [Mengram](https://mengram.io) into a Kimi agent as
tools so it **remembers across sessions**: facts about you, and procedures it
learned from past failures (with a success/failure track record).

Kimi K3 speaks the OpenAI API, so it's a drop-in — same `openai` client, a
different `base_url`. And Mengram is multilingual (works in Chinese and
English), which fits a Kimi-first stack.

```
User: "deploy the app the way we always do"

  Kimi calls get_procedures("deploy")
  → deploy-railway (v3, 11 ok / 1 failed) — verify first: run migrations before push

  Kimi: "Based on your learned procedure (failed once in April on migrations),
  I'll run migrations first, then push."
```

## Run it

```bash
# Offline walkthrough — no keys
python kimi_agent.py --offline

# Real — Moonshot key (platform.moonshot.ai) + free Mengram key (mengram.io)
export MOONSHOT_API_KEY=...  MENGRAM_API_KEY=om-...
python kimi_agent.py "what do you know about my deploy setup?"
pip install openai mengram-ai   # if not already installed
```

## How it works

The agent runs on Kimi K3 (`base_url=https://api.moonshot.ai/v1`, `model=kimi-k3`)
and gets three memory tools backed by Mengram:

- `search_memory` — semantic recall of facts, preferences, past events
- `save_memory` — persist a durable fact/decision
- `get_procedures` — retrieve learned workflows with their track record and the
  preconditions to verify (so the agent stops repeating a mistake it already made)

Mengram's core is Apache-2.0 with a free tier; it also works with Claude Code,
Cursor, and any MCP client. Built against Moonshot's documented OpenAI-compatible
API — verify the current model id and pricing at platform.moonshot.ai.
