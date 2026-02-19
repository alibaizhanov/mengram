# Customer Support Agent — CrewAI + Mengram

An interactive support agent that remembers customer history, preferences, and past tickets. Built with [CrewAI](https://crewai.com) and powered by Mengram's memory system.

**Run it twice** to see the difference between a "blank slate" and a "returning customer".

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

The agent has 5 Mengram memory tools:

| Tool | What it does |
|------|-------------|
| `mengram_search` | Searches all 3 memory types (semantic, episodic, procedural) |
| `mengram_remember` | Saves new information to memory |
| `mengram_profile` | Loads the customer's cognitive profile |
| `mengram_save_workflow` | Records support workflows for reuse |
| `mengram_workflow_feedback` | Reports success/failure on procedures |

With `verbose=True`, you'll see CrewAI's reasoning as the agent decides which tools to use — searching memory before responding, saving new details after learning them.

## Try This

**First run:**
```
Customer: Hi, I'm Jane. I ordered a laptop last week and it hasn't arrived.
Customer: My order number is #12847. I paid extra for express shipping.
Customer: I'm really frustrated — I needed it for a presentation tomorrow.
```

**Second run:**
```
Customer: Hi, it's Jane again.
Customer: The laptop finally arrived but the screen is cracked.
```

On the second run, the agent already knows Jane, her order history, and her frustration — and responds accordingly.

## How It Works

1. Every customer message triggers the agent to **search memory first**
2. Mengram returns relevant facts, past episodes, and known workflows
3. The agent crafts a personalized response using this context
4. New information is **saved to memory** for future interactions
5. Over time, Mengram builds a **cognitive profile** — a summary of the customer's communication style, preferences, and history

## What to Try Next

- Add `mengram_save_workflow` calls to teach the agent support procedures
- Report procedure outcomes with `mengram_workflow_feedback`

## Learn More

- [Mengram Website](https://mengram.io)
- [API Reference](https://mengram.io/docs)
- [GitHub](https://github.com/alibaizhanov/mengram)
