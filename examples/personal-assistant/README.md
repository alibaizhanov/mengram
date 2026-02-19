# Personal Assistant — LangChain + Mengram

A personal assistant that knows you — your preferences, relationships, habits, and history. Built with [LangChain](https://langchain.com) and powered by Mengram's memory system.

The more you chat, the smarter it gets.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API keys
cp .env.example .env
# Edit .env and add your keys

# 3. Run
python main.py
```

## What You'll See

The assistant uses three Mengram components:

| Component | What it does |
|-----------|-------------|
| `MengramChatMessageHistory` | Auto-saves every conversation to Mengram |
| `MengramRetriever` | Retrieves context from all 3 memory types (semantic, episodic, procedural) |
| `get_mengram_profile_prompt` | Loads an AI-generated cognitive profile as the system prompt |

On the first run, the assistant starts fresh. After a few conversations, Mengram builds a **cognitive profile** — an AI-generated summary of your preferences, communication style, and key facts.

## Try This

**Session 1:**
```
You: I'm Alex, a software engineer at Acme Corp. I work on the backend team.
You: I prefer Python over JavaScript, and I'm allergic to peanuts.
You: My wife Sarah and I are planning a trip to Japan in March.
```

**Session 2:**
```
You: What do you know about me?
You: Can you suggest a restaurant for dinner tonight?
You: Help me plan my upcoming trip.
```

In session 2, the assistant remembers Alex's job, food allergy, family, and travel plans — and tailors every response.

## How It Works

1. **Cognitive profile** loads as the system prompt — the LLM "knows" you before you say anything
2. **MengramRetriever** searches all 3 memory types for relevant context on each message
3. Retrieved memories are injected into the prompt as additional context
4. **MengramChatMessageHistory** saves the full conversation to Mengram after each exchange
5. Mengram's background extraction automatically identifies entities, facts, and relationships

## What to Try Next

- Use `create_mengram_chain()` for a single-line setup that bundles profile + retriever + LLM
- Add `MengramRetriever(memory_types=["episodic"])` to search only past episodes
- Try `mem.search_all()` to see raw results from all memory types

## Learn More

- [Mengram Documentation](https://mengram.io/docs)
- [LangChain Integration Guide](https://mengram.io/docs/integrations/langchain)
