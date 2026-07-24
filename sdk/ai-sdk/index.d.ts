import type { Tool } from 'ai';
import type { MengramClient } from 'mengram-ai';

export interface MengramToolsConfig {
  /** Mengram API key (om-...). Get a free one at https://mengram.io */
  apiKey?: string;
  /** Scope memories to an end-user of your app (multi-user isolation). */
  userId?: string;
  /** Override the API base URL (self-hosted instances). */
  baseUrl?: string;
  /** Max results per tool call. Default 5. */
  limit?: number;
  /** Preconfigured MengramClient — overrides apiKey/baseUrl. */
  client?: MengramClient;
}

export interface MengramTools {
  /** Semantic search over facts, preferences, events, decisions. */
  searchMemory: Tool;
  /** Save a durable fact/preference/event to long-term memory. */
  addMemory: Tool;
  /** Retrieve learned workflows with steps, track record, and preconditions. */
  getProcedures: Tool;
}

/** Memory tools for generateText / streamText `tools` parameter. */
export declare function mengramTools(config?: MengramToolsConfig): MengramTools;

/**
 * Fetch the user's Cognitive Profile as a system prompt string.
 * Returns '' when the user has no memories yet.
 */
export declare function retrieveProfile(config?: MengramToolsConfig): Promise<string>;

/** Persist a finished conversation ({ role, content }[]) to memory. */
export declare function saveConversation(
  messages: Array<{ role: string; content: unknown }>,
  config?: MengramToolsConfig
): Promise<{ status: string; message?: string; job_id?: string }>;
