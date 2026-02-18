# Mengram — Architecture Overview

## Concept

You chat with Claude (or any LLM). The system **automatically** extracts
knowledge from conversations and builds a structured memory — your second brain.

## How It Works

```
                         ┌──────────────────────┐
                         │   You chat with       │
                         │   Claude / GPT /      │
                         │   any LLM             │
                         └──────────┬───────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   CONVERSATION EXTRACTOR       │
                    │                               │
                    │ Analyzes conversation:         │
                    │ • Who is mentioned? (people)   │
                    │ • Which projects?              │
                    │ • Which technologies?          │
                    │ • What facts?                  │
                    │ • What connections between?    │
                    └───────────────┬───────────────┘
                                    │ extracted knowledge
                                    ▼
                    ┌───────────────────────────────┐
                    │     VAULT MANAGER              │
                    │                               │
                    │ Creates/updates .md files:     │
                    │ • Ali.md ← new facts           │
                    │ • PostgreSQL.md ← update       │
                    │ • Project Alpha.md ← create    │
                    │ • [[links]] between files      │
                    └───────────────┬───────────────┘
                                    │ .md files
                                    ▼
                    ┌───────────────────────────────┐
                    │      OBSIDIAN VAULT            │
                    │                               │
                    │  📄 Ali.md                     │
                    │  📄 Uzum Bank.md               │
                    │  📄 Project Alpha.md           │
                    │  📄 PostgreSQL.md              │
                    │  📄 Spring Boot.md             │
                    │                               │
                    │  Open in Obsidian!             │
                    │  → Graph View                  │
                    │  → Edit manually               │
                    │  → Add notes by hand           │
                    └───────────────┬───────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │     MEMORY RETRIEVAL           │
                    │                               │
                    │ On next conversation:          │
                    │ Claude asks "what do I know    │
                    │ about this user?"              │
                    │ → Searches vault               │
                    │ → Returns context              │
                    │ → Claude responds smarter      │
                    └───────────────────────────────┘
```

## Project Structure

```
mengram/
├── engine/
│   ├── extractor/
│   │   ├── conversation_extractor.py  # Knowledge extraction from conversations
│   │   └── llm_client.py             # LLM client (Claude/OpenAI/Ollama)
│   ├── vault_manager/
│   │   └── vault_manager.py          # Create/update .md files
│   ├── graph/
│   │   └── knowledge_graph.py        # Relation index (SQLite cache)
│   ├── vector/
│   │   ├── embedder.py               # Local embeddings
│   │   └── vector_store.py           # Semantic search
│   └── retrieval/
│       └── hybrid_search.py          # Context retrieval for LLM
├── api/
│   └── mcp_server.py                 # MCP Server (Claude Desktop / Cursor)
├── vault/                            # Auto-created — Obsidian vault
├── tests/
├── config.yaml                       # Settings (LLM provider, vault path, etc.)
└── README.md
```
