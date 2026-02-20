# Mengram — OpenClaw Integration

## Recommended: OpenClaw Plugin

The recommended way to use Mengram with OpenClaw is the **plugin** (not this skill):

```bash
openclaw plugins install openclaw-mengram
```

The plugin provides:
- **Auto-recall** — memories injected before every agent turn (via `before_agent_start` hook)
- **Auto-capture** — new info saved after every turn (via `agent_end` hook)
- 6 tools: `memory_search`, `memory_store`, `memory_forget`, `memory_profile`, `memory_procedures`, `memory_feedback`
- Slash commands: `/remember`, `/recall`, `/forget`
- CLI: `openclaw mengram search/stats/profile/procedures`
- All limits configurable via plugin config
- Graph RAG with 2-hop knowledge graph traversal

**Plugin repo:** [github.com/alibaizhanov/openclaw-mengram](https://github.com/alibaizhanov/openclaw-mengram)
**npm:** [openclaw-mengram](https://www.npmjs.com/package/openclaw-mengram)

### Setup

1. Get a free API key at [mengram.io](https://mengram.io)

2. Add to `~/.openclaw/openclaw.json`:

```json
{
  "plugins": {
    "entries": {
      "openclaw-mengram": {
        "enabled": true,
        "config": {
          "apiKey": "${MENGRAM_API_KEY}"
        }
      }
    },
    "slots": {
      "memory": "openclaw-mengram"
    }
  }
}
```

3. Set your API key: `export MENGRAM_API_KEY="om-your-key"`

4. Restart OpenClaw. Memory works automatically.

---

## Legacy: OpenClaw Skill

The files in this directory (`scripts/`, `SKILL.md`) are the legacy **skill** approach using bash scripts. Skills are passive — they require the agent to manually call tools. The plugin above is recommended because it has lifecycle hooks for fully automatic memory.

The skill is still functional if you prefer the simpler approach:

```bash
cp -r mengram-memory ~/.openclaw/skills/
```

## Links

- [mengram.io](https://mengram.io) — Get API key
- [GitHub](https://github.com/alibaizhanov/mengram) — Source code
- [API Docs](https://mengram.io/docs) — Full API reference
- [OpenClaw Plugin](https://github.com/alibaizhanov/openclaw-mengram) — Plugin repo

## License

Apache 2.0
