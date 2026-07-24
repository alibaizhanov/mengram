# mengram-ai-sdk

[Mengram](https://mengram.io) memory tools for the [Vercel AI SDK](https://ai-sdk.dev) — give your `generateText` / `streamText` agents long-term memory: **semantic** (facts), **episodic** (events), and **procedural** (workflows that learn from failures).

Unlike fact-only memory tools, Mengram's `getProcedures` returns step-by-step playbooks with a track record (11 successes, 1 failure) and **preconditions** — the assumptions that were violated when the workflow previously failed — so your agent stops repeating the same mistakes.

## Install

```bash
npm install mengram-ai-sdk ai zod
```

Grab a free API key at [mengram.io](https://mengram.io) (40 memory adds + 200 searches/mo, no card).

## Memory tools

```js
import { generateText } from 'ai';
import { mengramTools } from 'mengram-ai-sdk';

const { text } = await generateText({
  model: yourModel,
  tools: mengramTools({ apiKey: process.env.MENGRAM_API_KEY, userId: 'user-123' }),
  prompt: 'Deploy the app the way we always do.',
});
```

Three tools are exposed to the model:

| Tool | What it does |
|---|---|
| `searchMemory` | Semantic search over facts, preferences, events, decisions |
| `addMemory` | Save a durable fact to long-term memory |
| `getProcedures` | Retrieve learned workflows: steps, success/failure track record, preconditions to verify |

## Cognitive Profile as system prompt

One call returns a ready-to-use system prompt built from all three memory types:

```js
import { generateText } from 'ai';
import { retrieveProfile } from 'mengram-ai-sdk';

const system = await retrieveProfile({ apiKey: process.env.MENGRAM_API_KEY, userId: 'user-123' });
// '' if the user has no memories yet — safe to pass unconditionally

const { text } = await generateText({ model: yourModel, system, prompt: '...' });
```

## Save conversations

Extraction runs server-side — pass raw messages, Mengram distills facts, events, and workflows:

```js
import { saveConversation } from 'mengram-ai-sdk';

await saveConversation(result.response.messages, {
  apiKey: process.env.MENGRAM_API_KEY,
  userId: 'user-123',
});
```

## Multi-user apps

Pass your end-user's id as `userId` — each user gets isolated facts, events, workflows, and profile under one API key. See [multi-user docs](https://mengram.io/for-agents).

## Options

```js
mengramTools({
  apiKey: 'om-...',        // or MENGRAM_API_KEY via your own env handling
  userId: 'user-123',      // multi-user isolation (optional)
  limit: 5,                // max results per tool call
  baseUrl: 'https://...',  // self-hosted instances
});
```

## Links

- [Docs](https://docs.mengram.io) · [REST API](https://mengram.io/docs) · [GitHub](https://github.com/alibaizhanov/mengram) · [Python SDK](https://pypi.org/project/mengram-ai/) · [JS SDK](https://www.npmjs.com/package/mengram-ai)

Apache 2.0
