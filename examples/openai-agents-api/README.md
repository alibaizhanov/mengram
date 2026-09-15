# OpenAI Agents API + Mengram — memory per end user

OpenAI's Agents API (public beta, September 2026) runs agents on the Codex
harness with durable sessions, auto-compaction and a sandbox memory. That
memory is per **workspace**. A product with returning users needs memory per
**user** — what this customer prefers, what was decided with them, what broke
last time — and it has to be the same memory whether the agent runs on
OpenAI, on Claude, or in your own code.

Mengram is that memory, attached as an MCP server. One connection per end
user, scoped by one header:

```python
MCPServerStreamableHttp(params={
    "url": "https://mengram.io/mcp",
    "headers": {"Authorization": "Bearer om-...", "X-Mengram-User": "cust_1042"},
})
```

Every `remember` / `recall` / `context_for` on that connection belongs to
`cust_1042`, isolated from every other user of your product. There is no
cap on the number of end users on any plan.

## Hosted MCP tool (Responses / Agents API JSON)

If you let OpenAI manage the connection instead:

```json
{
  "type": "mcp",
  "server_label": "mengram",
  "server_url": "https://mengram.io/mcp?user_id=cust_1042",
  "authorization": "Bearer om-...",
  "allowed_tools": ["context_for", "recall", "remember"],
  "require_approval": "never"
}
```

`user_id` can travel as a query parameter when headers are not configurable.

## Keep the context small

`context_for` takes `max_tokens` (default 1200): memory is cut in rank order
to fit and the pack ends with one line saying what was left out. A support
bot that used to send 4,500 tokens of history per request sends the 600 that
matter.

## Run it

```bash
pip install openai-agents mengram-ai
export OPENAI_API_KEY=sk-... MENGRAM_API_KEY=om-...
python agent.py cust_1042 "hi, it's me again — same car as last time?"
```

Docs: https://docs.mengram.io/openai-agents
