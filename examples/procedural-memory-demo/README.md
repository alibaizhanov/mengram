# An agent that stops repeating its own mistakes

A 100-line demo of **procedural memory**: the same agent runs the same deploy
task twice. The first time it botches it (pushes before migrations, DB breaks).
The second time it recalls what it learned, verifies the precondition, and ships
clean — without anyone editing an instructions file.

```
RUN 1 — first time, no memory
→ Agent task: deploy the app to production
🧠 No memory of this task. Agent proceeds on instinct.
→ Result: pushed before migrations — schema mismatch, DB crashed ❌
💾 Failure recorded. The agent will remember this next time.

RUN 2 — same task, now with memory
🧠 Recalled procedure 'deploy to production' v2 (0 ok / 1 failed)
   Precondition to verify first: run database migrations before pushing
→ Agent verifies migrations BEFORE pushing.
→ Result: migrations applied, then pushed — deploy healthy ✅

✅ The agent failed once, learned, and did NOT repeat the mistake.
   That's procedural memory. A static instructions file can't do this.
```

## Run it in 10 seconds (no account)

```bash
python deploy_agent.py --offline
```

Uses a local stand-in so you can see the concept immediately.

## Run it for real (with Mengram)

```bash
pip install mengram-ai
MENGRAM_API_KEY=om-... python deploy_agent.py --cloud
```

Now the failure is extracted server-side into a versioned procedure with a
success/fail track record and a precondition — and recall on run 2 comes from
the real memory layer. Free key (no card) at **[mengram.io](https://mengram.io)**.

## Why this isn't a CLAUDE.md

A static instructions file records *what to do*. It goes stale, and nobody
updates it with evidence when something fails. Procedural memory records *what
went wrong and why*, versions the workflow, and surfaces the precondition at
recall time — so the agent stops re-deriving the fix and stops repeating the
mistake. Works with Claude Code, Cursor, and any MCP client, per end-user via
`user_id`.

- Docs: https://docs.mengram.io
- Memory API for agent builders: https://mengram.io/for-agents
- Python / JS SDKs, Apache-2.0 core: https://github.com/alibaizhanov/mengram
