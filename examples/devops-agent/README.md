# DevOps Agent — Experience-Driven Deployment Procedures

A scripted demo showing how Mengram learns deployment procedures from team conversations and **evolves them automatically** when failures are reported.

No LLM API key needed — Mengram handles all extraction server-side.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API key
cp .env.example .env
# Edit .env and add your Mengram API key (get one at https://mengram.io/dashboard)

# 3. Run
python main.py
```

## What You'll See

1. **Procedure extraction** — A deployment conversation is sent to Mengram, which automatically extracts a step-by-step procedure
2. **Procedure search** — The extracted procedure is retrieved with semantic search
3. **Failure reporting** — A production failure is reported with root cause context
4. **Automatic evolution** — Mengram evolves the procedure to prevent the failure from recurring
5. **Version history** — Track how procedures improve over time
6. **Unified search** — Search across semantic, episodic, and procedural memory simultaneously

## How It Works

Mengram's **experience-driven procedures** learn from real outcomes:

- When a conversation describes a workflow, Mengram extracts it as a procedure
- When you report a failure with context, Mengram rewrites the procedure to address the root cause
- Each evolution is versioned — you can see exactly what changed and why
- Success/failure counts build a reliability score over time

This is the core differentiator: procedures that **get better from experience**, not just static runbooks.

## What to Try Next

- Run the demo multiple times — watch the procedure accumulate more context
- Change the failure scenario in `main.py` to see different evolutions
- Use `mem.procedures()` without a query to list all learned procedures
- Try `mem.search_all()` with different queries to explore unified search

## Learn More

- [Mengram Documentation](https://mengram.io/docs)
- [CloudMemory SDK Reference](https://mengram.io/docs/sdk/python)
- [Experience-Driven Procedures](https://mengram.io/docs/procedures)
