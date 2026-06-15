# Mengram Claude Code plugin

Persistent memory for Claude Code that survives `/clear`, machine switches, and team handoffs.

Unlike `CLAUDE.md` (static) or filesystem memory (per-machine), Mengram is a **hosted memory backend** with hybrid retrieval (vector + BM25 + RRF), temporal decay, and cross-device sync. Same memory in Claude Code on your laptop, on your work machine, on Cursor, on ChatGPT.

## What's inside

| Component | What it does |
|---|---|
| **MCP server** (`mengram`) | 30 tools — `add`, `search`, `search_all`, `recall`, `profile`, `reflect`, etc. Invoke via `/mengram:*` |
| **SessionStart hook** | Prepends your cognitive profile to Claude's context on session open |
| **Stop hook** | Persists the conversation transcript at end-of-turn for fact / episode / procedure extraction |
| **Skill** | Tells Claude when to recall and when to capture |

## Setup

```bash
# 1. Get a free API key (40 adds + 200 searches/month free)
open https://mengram.io

# 2. Set the key once
export MENGRAM_API_KEY=om-your-key-here

# 3. Install the plugin
claude plugin install mengram
```

That's it. New sessions will start with your memory loaded; conversations will be persisted automatically.

## Pricing

Free tier: 40 adds + 200 searches per month. Paid tiers from $5/mo. Self-host is supported (Apache 2.0) — see https://github.com/alibaizhanov/mengram.

## What gets captured

Conversations transit Mengram's extraction pipeline (`POST /v1/add_text`), which extracts:
- **Facts** — durable preferences, constraints, decisions
- **Episodes** — what happened in a session, ranked by importance
- **Procedures** — multi-step workflows with success/recency weighting

Retrieval combines vector similarity (OpenAI text-embedding-3-large), BM25 keyword match, and Reciprocal Rank Fusion. Facts decay via the Ebbinghaus forgetting curve so stale context doesn't drown new context.

## Privacy

- **What's sent:** conversation transcripts (last ~8KB per turn) and profile lookups
- **What's stored:** extracted entities, facts, episodes, procedures in Mengram's Postgres
- **Source attribution:** all hook-captured content is tagged `source=claude_code`
- **Disable per-session:** unset `MENGRAM_API_KEY` before launching Claude Code

Mengram is Apache 2.0. Self-host to keep all data on your own infra.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No profile loaded on session start | Check `echo $MENGRAM_API_KEY`. Hook silently no-ops without a key. |
| Conversations not appearing in dashboard | Stop hook runs at end-of-turn — fires when Claude finishes responding, not when you press Ctrl-C |
| MCP tools not visible | Restart Claude Code after installing the plugin. Run `claude mcp list` to verify `mengram` is connected. |

## License

Apache 2.0. See LICENSE in the parent repo.
