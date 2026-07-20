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

# 2. Save the key once — hooks and MCP pick it up from here in every session
mkdir -p ~/.mengram && echo '{"api_key": "om-your-key-here"}' > ~/.mengram/config.json

# 3. Install the plugin
claude plugin marketplace add alibaizhanov/mengram
claude plugin install mengram@mengram
```

(`export MENGRAM_API_KEY=om-...` in your shell profile works too — the env var
always wins over the config file.)

### Try it (30 seconds)

1. Tell Claude something worth remembering: *"I'm building a fintech app in React, deadline is October."*
2. Run `/clear` — or close the terminal and start a fresh session.
3. Ask: *"What am I building?"* — Claude answers from memory.

That's the whole product. Every session from now on starts with your context loaded.

### Skip the cold start — import your existing history

Your past Claude Code sessions are already on disk. Feed them in and memory starts full, not empty:

```bash
pip install mengram-ai
mengram import claude-code        # imports your ~20 most recent sessions
```

Secrets (API keys, tokens) are redacted before anything leaves your machine. Re-runs skip already-imported sessions.

## Pricing

Free tier: 40 adds + 200 searches per month. Paid tiers from $5/mo. Self-host is supported (Apache 2.0) — see https://github.com/alibaizhanov/mengram.

## What gets captured

Conversations transit Mengram's extraction pipeline (`POST /v1/add_text`), which extracts:
- **Facts** — durable preferences, constraints, decisions
- **Episodes** — what happened in a session, ranked by importance
- **Procedures** — multi-step workflows with success/recency weighting

Retrieval combines vector similarity (OpenAI text-embedding-3-large), BM25 keyword match, and Reciprocal Rank Fusion. Facts decay via the Ebbinghaus forgetting curve so stale context doesn't drown new context.

## Self-check and heartbeat

**First-run self-check:** until the plugin has verified one successful round-trip to the API, failures are loud — a one-line message in Claude Code tells you exactly what's broken (no key found / key found but verification failed). After the first success, failures go back to silent so an outage never spams you.

**Opt-in heartbeat:** set `MENGRAM_HEARTBEAT=25` (env) or `"heartbeat": 25` in `~/.mengram/config.json` and every 25th successful save shows one line — `[mengram] heartbeat: 150 conversations saved to memory so far`. Silence-when-enabled means something is wrong; that's the point.

## Privacy

- **What's sent:** conversation transcripts (last ~8KB per turn) and profile lookups
- **What's stored:** extracted entities, facts, episodes, procedures in Mengram's Postgres
- **Source attribution:** all hook-captured content is tagged `source=claude_code`
- **Disable per-session:** unset `MENGRAM_API_KEY` before launching Claude Code

Mengram is Apache 2.0. Self-host to keep all data on your own infra.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No profile loaded on session start | Check `cat ~/.mengram/config.json` or `echo $MENGRAM_API_KEY` — hooks look in both (env wins). Without a key they silently no-op. |
| Conversations not appearing in dashboard | Stop hook runs at end-of-turn — fires when Claude finishes responding, not when you press Ctrl-C |
| MCP tools not visible | Restart Claude Code after installing the plugin. Run `claude mcp list` to verify `mengram` is connected. |
| Windows: saves never arrive | Fixed in the current version — older hook scripts required a real `python3`, which the Microsoft Store stub isn't. Update the plugin. |

## License

Apache 2.0. See LICENSE in the parent repo.
