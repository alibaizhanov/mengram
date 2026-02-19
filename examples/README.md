# Mengram Agent Templates

Ready-to-run AI agents that showcase Mengram's 3 memory types. Each template is self-contained — clone, set API key, run in 5 minutes.

## Prerequisites

1. **Get a free Mengram API key** at [mengram.io](https://mengram.io) (no credit card)
2. Python 3.10+
3. An OpenAI API key (for Customer Support and Personal Assistant templates)

## Templates

### 1. DevOps Agent — *Experience-Driven Procedures*

**Stack:** CloudMemory SDK (no LLM key needed)

A scripted demo that teaches Mengram a deployment procedure, reports a failure, and watches the procedure evolve automatically. The best demo of Mengram's unique differentiator.

```bash
cd devops-agent
pip install -r requirements.txt
export MENGRAM_API_KEY=om-...
python main.py
```

**You'll see:** procedure extraction from conversation, failure-driven evolution, version history, unified search across all 3 memory types.

---

### 2. Customer Support Agent — *CrewAI + Memory Tools*

**Stack:** CrewAI + Mengram (needs `OPENAI_API_KEY`)

An interactive support agent with 5 Mengram tools. Watch CrewAI's verbose output as the agent decides when to search memory, load the customer profile, and save new information.

```bash
cd customer-support-agent
pip install -r requirements.txt
export MENGRAM_API_KEY=om-...
export OPENAI_API_KEY=sk-...
python main.py
```

**Try it:** Run twice — the second time, the agent remembers the first conversation.

---

### 3. Personal Assistant — *LangChain + Cognitive Profile*

**Stack:** LangChain + Mengram (needs `OPENAI_API_KEY`)

A chat assistant that loads your Cognitive Profile as its system prompt, retrieves context from all 3 memory types, and saves every conversation automatically.

```bash
cd personal-assistant
pip install -r requirements.txt
export MENGRAM_API_KEY=om-...
export OPENAI_API_KEY=sk-...
python main.py
```

**You'll see:** the assistant already knows who you are from past conversations and gets smarter with every message.

## How Mengram Works

Every `add()` call automatically extracts 3 types of memory:

- **Semantic** — facts, preferences, relationships (*"prefers Python"*, *"allergic to peanuts"*)
- **Episodic** — events with context and outcomes (*"deployed on Friday, production crashed"*)
- **Procedural** — step-by-step workflows with success/failure tracking (*"deploy: test → build → push → monitor"*)

Procedures **evolve from experience**: report a failure with context, and Mengram rewrites the procedure to prevent it from recurring. Every evolution is versioned.

## Links

- [Mengram Website](https://mengram.io)
- [Get API Key](https://mengram.io/dashboard)
- [API Reference](https://mengram.io/docs)
- [GitHub](https://github.com/alibaizhanov/mengram)
