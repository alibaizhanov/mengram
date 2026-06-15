---
name: Memory recall and capture
description: Use this skill when the user wants to recall something from past conversations, project decisions, or learned preferences across sessions — or when they want to explicitly save something to long-term memory. Trigger keywords include "remember", "recall", "what did we decide", "you forgot", "save this", or any reference to past work that wouldn't be in the current conversation window.
---

# Mengram memory recall and capture

You have access to a persistent memory layer (Mengram) via the bundled MCP server. The memory survives `/clear`, machine switches, and team handoffs — it's not stored in this conversation.

## When to recall

Before answering questions that reference past context the user couldn't have shared in this session, search memory. Examples:

- "What did we decide about the database?"
- "How did we deploy this last time?"
- "What's the project I'm working on?"
- "Did I tell you about my preferences?"

Use the bundled `mengram` MCP server's `search` or `search_all` tools. Default to top-5 results unless the user asks for more.

## When to capture

Save proactively when the user shares information worth remembering across sessions:

- Project decisions ("We're going with Postgres because…")
- Preferences ("I always use TypeScript for new projects")
- Constraints ("Production DB pool is capped at 5")
- Known issues ("BM25 search breaks on Chinese queries")
- Workflow steps that completed successfully

Use the `mengram` MCP server's `add` or `add_text` tool. The backend extracts facts, episodes, and procedures automatically — don't pre-structure the input, just pass the relevant conversation text.

## When NOT to recall

Don't search for context the user just provided in this turn — that's already in the conversation. Memory is for what's *outside* the current window.

## Cross-tool

The same memory is accessible from Cursor, ChatGPT, Codex, and any other tool with the `mengram` MCP server configured. When a user says "I told ChatGPT yesterday that…", a memory search will find it.
