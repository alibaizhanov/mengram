# n8n + Mengram: AI Agent with Persistent Memory

Add long-term memory to any n8n AI workflow. Your agent remembers users across sessions — preferences, past conversations, resolved issues.

## How It Works

```
Webhook → Search Memories → Build Prompt → AI Response → Save to Memory → Respond
```

1. **Webhook** receives a chat message with `user_id`
2. **Search Memories** calls Mengram API to find relevant past context
3. **Build Prompt** injects memories into the system prompt
4. **AI Response** generates a response with full context (uses OpenAI, swap for any LLM)
5. **Save to Memory** stores the conversation — Mengram auto-extracts facts and deduplicates
6. **Respond** returns the AI response

## Setup (5 minutes)

### 1. Get a Mengram API Key

Sign up at [mengram.io](https://mengram.io) and copy your API key (`om-...`).

### 2. Import the Workflow

- Open n8n
- Go to **Workflows → Import from File**
- Select `mengram-memory-agent.json`

### 3. Configure Credentials

Create two **Header Auth** credentials in n8n:

**Mengram API Key:**
- Name: `Mengram API Key`
- Header Name: `Authorization`
- Header Value: `Bearer om-your-api-key-here`

**OpenAI API Key:**
- Name: `OpenAI API Key`
- Header Name: `Authorization`
- Header Value: `Bearer sk-your-openai-key-here`

Assign them to the corresponding nodes (Search Memories, Save to Memory, AI Response).

### 4. Activate and Test

Activate the workflow and send a POST request:

```bash
curl -X POST http://localhost:5678/webhook/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hi! I prefer Python over JavaScript and I use Railway for hosting.",
    "user_id": "user-123"
  }'
```

Send another message — the agent now remembers:

```bash
curl -X POST http://localhost:5678/webhook/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What hosting platform should I deploy to?",
    "user_id": "user-123"
  }'
```

The agent will reference Railway since it remembers your preference.

## Swap the LLM

The workflow uses OpenAI `gpt-4o-mini` by default. To use a different LLM:

- **Anthropic**: Change the URL to `https://api.anthropic.com/v1/messages` and adjust the request body
- **Ollama** (local): Change the URL to `http://localhost:11434/api/chat`
- **Any OpenAI-compatible API**: Just change the URL and model name

## Nodes Overview

| Node | Type | What it does |
|------|------|-------------|
| Webhook | Trigger | Receives `{ message, user_id }` via POST |
| Search Memories | HTTP Request | `POST /v1/search` — finds relevant memories |
| Build Prompt | Code | Formats memories into a system prompt |
| AI Response | HTTP Request | Calls OpenAI (or any LLM) with memory-augmented prompt |
| Extract Response | Code | Extracts the assistant's reply |
| Save to Memory | HTTP Request | `POST /v1/add` — stores the conversation |
| Respond | Webhook Response | Returns the AI response to the caller |

## Links

- [Mengram Docs](https://mengram.io/docs)
- [Mengram GitHub](https://github.com/alibaizhanov/mengram)
- [API Reference](https://mengram.io/docs/api-reference)
