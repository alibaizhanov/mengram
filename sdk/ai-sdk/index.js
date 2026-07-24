/**
 * Mengram memory tools for the Vercel AI SDK.
 *
 * Usage:
 *   import { generateText } from 'ai';
 *   import { mengramTools, retrieveProfile } from 'mengram-ai-sdk';
 *
 *   const system = await retrieveProfile({ apiKey: 'om-...', userId: 'ali' });
 *   const { text } = await generateText({
 *     model: yourModel,
 *     system,
 *     tools: mengramTools({ apiKey: 'om-...', userId: 'ali' }),
 *     prompt: 'Deploy the app like we always do.',
 *   });
 */
import { tool } from 'ai';
import { z } from 'zod';
import mengram from 'mengram-ai';

const { MengramClient } = mengram;

function resolveClient(config = {}) {
  if (config.client) return config.client;
  if (!config.apiKey) {
    throw new Error('mengram-ai-sdk: pass { apiKey } (om-...) or a preconfigured { client }');
  }
  const options = {};
  if (config.baseUrl) options.baseUrl = config.baseUrl;
  return new MengramClient(config.apiKey, options);
}

/**
 * Memory tools for generateText / streamText.
 * Returns { searchMemory, addMemory, getProcedures }.
 */
export function mengramTools(config = {}) {
  const client = resolveClient(config);
  const userId = config.userId;
  const limit = config.limit ?? 5;

  return {
    searchMemory: tool({
      description:
        'Search the user\'s long-term memory for facts, preferences, past events, and decisions. ' +
        'Use before answering anything that may depend on who the user is or what happened before.',
      inputSchema: z.object({
        query: z.string().describe('What to look for, e.g. "database preferences" or "last deploy incident"'),
      }),
      execute: async ({ query }) => {
        const results = await client.search(query, { userId, limit });
        if (!results.length) return { found: 0, results: [] };
        return {
          found: results.length,
          results: results.map((r) => ({
            entity: r.entity,
            type: r.type,
            facts: r.facts,
          })),
        };
      },
    }),

    addMemory: tool({
      description:
        'Save an important new fact, preference, event, or decision to the user\'s long-term memory ' +
        'so future sessions remember it. Use sparingly for durable information, not small talk.',
      inputSchema: z.object({
        content: z.string().describe('The information to remember, stated plainly'),
      }),
      execute: async ({ content }) => {
        const res = await client.addText(content, { userId, source: 'ai-sdk' });
        return { status: res.status };
      },
    }),

    getProcedures: tool({
      description:
        'Retrieve the user\'s learned workflows (procedures) relevant to a task — step-by-step ' +
        'playbooks that evolved from past successes and failures, with preconditions to verify. ' +
        'Use before performing a multi-step task the user has likely done before (deploys, releases, setups).',
      inputSchema: z.object({
        task: z.string().describe('The task at hand, e.g. "deploy to production"'),
      }),
      execute: async ({ task }) => {
        const procedures = await client.procedures({ query: task, limit, userId });
        if (!procedures.length) return { found: 0, procedures: [] };
        return {
          found: procedures.length,
          procedures: procedures.map((p) => ({
            name: p.name,
            version: p.version,
            trigger: p.trigger_condition,
            steps: p.steps,
            track_record: { successes: p.success_count, failures: p.fail_count },
            preconditions: p.metadata?.preconditions ?? [],
          })),
        };
      },
    }),
  };
}

/**
 * Fetch the user's Cognitive Profile as a ready-to-use system prompt.
 * Returns '' when the user has no memories yet — safe to pass to `system` unconditionally.
 */
export async function retrieveProfile(config = {}) {
  const client = resolveClient(config);
  const profile = await client.getProfile(config.userId);
  if (profile.status !== 'ok' || !profile.system_prompt) return '';
  return profile.system_prompt;
}

/**
 * Persist a finished conversation to memory (extraction runs server-side).
 * `messages` uses the standard { role, content } shape; non-string content is skipped.
 */
export async function saveConversation(messages, config = {}) {
  const client = resolveClient(config);
  const simple = (messages || [])
    .filter((m) => (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim())
    .map((m) => ({ role: m.role, content: m.content }));
  if (!simple.length) return { status: 'skipped', message: 'no text messages to save' };
  return client.add(simple, { userId: config.userId, source: 'ai-sdk' });
}
