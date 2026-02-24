export interface MengramOptions {
  baseUrl?: string;
  timeout?: number;
}

export interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface MemoryOptions {
  userId?: string;
  agentId?: string;
  runId?: string;
  appId?: string;
  expirationDate?: string;
}

export interface SearchOptions extends MemoryOptions {
  limit?: number;
  graphDepth?: number;
}

export interface AddResult {
  status: string;
  message?: string;
  job_id?: string;
}

export interface SearchResult {
  entity: string;
  type: string;
  score: number;
  facts: string[];
  knowledge: any[];
  relations: any[];
}

export interface Entity {
  name: string;
  type: string;
  facts: string[];
  knowledge: any[];
  relations: any[];
  created_at: string;
  updated_at: string;
}

export interface Stats {
  entities: number;
  facts: number;
  knowledge: number;
  relations: number;
  embeddings: number;
  by_type: Record<string, number>;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  active: boolean;
  created_at: string | null;
  last_used: string | null;
}

export interface JobStatus {
  status: 'pending' | 'processing' | 'completed' | 'failed';
  result?: any;
  error?: string;
}

export interface CognitiveProfile {
  user_id: string;
  system_prompt: string;
  facts_used: number;
  last_updated: string | null;
  status: 'ok' | 'no_data' | 'no_facts' | 'no_llm_key' | 'error';
  error?: string;
}

export interface Episode {
  id: string;
  summary: string;
  context: string | null;
  outcome: string | null;
  participants: string[];
  emotional_valence: 'positive' | 'negative' | 'neutral' | 'mixed';
  importance: number;
  linked_procedure_id: string | null;
  failed_at_step: number | null;
  score?: number;
  created_at: string | null;
  memory_type?: 'episodic';
}

export interface Procedure {
  id: string;
  name: string;
  trigger_condition: string | null;
  steps: Array<{ step: number; action: string; detail: string }>;
  entity_names: string[];
  success_count: number;
  fail_count: number;
  version: number;
  parent_version_id?: string | null;
  evolved_from_episode?: string | null;
  is_current?: boolean;
  score?: number;
  last_used: string | null;
  created_at?: string | null;
  updated_at: string | null;
  memory_type?: 'procedural';
}

export interface ProcedureEvolutionEntry {
  id: string;
  procedure_id: string;
  episode_id: string | null;
  change_type: 'step_added' | 'step_removed' | 'step_modified' | 'step_reordered' | 'auto_created';
  diff: Record<string, any>;
  version_before: number;
  version_after: number;
  created_at: string | null;
}

export interface ProcedureHistoryResult {
  versions: Procedure[];
  evolution_log: ProcedureEvolutionEntry[];
}

export interface FeedbackResult {
  id: string;
  name: string;
  success_count: number;
  fail_count: number;
  feedback: 'success' | 'failure';
  evolution_triggered: boolean;
}

export interface UnifiedSearchResult {
  semantic: SearchResult[];
  episodic: Episode[];
  procedural: Procedure[];
}

export interface Webhook {
  id: number;
  url: string;
  name: string;
  event_types: string[];
  active: boolean;
  trigger_count: number;
  last_triggered: string | null;
  last_error: string | null;
}

export interface BillingInfo {
  plan: 'free' | 'pro' | 'business';
  status: string;
  current_period_end: string | null;
  usage: Record<string, number>;
  quotas: Record<string, number>;
  rate_limit: number;
}

export declare class MengramError extends Error {
  statusCode: number;
  constructor(message: string, statusCode: number);
}

export declare class QuotaExceededError extends MengramError {
  action: string;
  limit: number;
  current: number;
  plan: string;
  constructor(detail: { action: string; limit: number; current: number; plan: string });
}

export declare class MengramClient {
  constructor(apiKey: string, options?: MengramOptions);

  // Memory
  add(messages: Message[], options?: MemoryOptions): Promise<AddResult>;
  addText(text: string, options?: MemoryOptions): Promise<AddResult>;
  search(query: string, options?: SearchOptions): Promise<SearchResult[]>;
  getAll(options?: MemoryOptions): Promise<Entity[]>;
  getAllFull(options?: { userId?: string }): Promise<Entity[]>;
  get(name: string, options?: { userId?: string }): Promise<Entity | null>;
  delete(name: string, options?: { userId?: string }): Promise<boolean>;
  fixEntityType(name: string, newType: string, options?: { userId?: string }): Promise<{status: string}>;
  stats(options?: { userId?: string }): Promise<Stats>;
  graph(options?: { userId?: string }): Promise<{ nodes: any[]; edges: any[] }>;
  getProfile(userId?: string, options?: { force?: boolean }): Promise<CognitiveProfile>;
  timeline(options?: { after?: string; before?: string; limit?: number }): Promise<any[]>;

  // Episodic Memory
  episodes(options?: { query?: string; limit?: number; after?: string; before?: string }): Promise<Episode[]>;

  // Procedural Memory
  procedures(options?: { query?: string; limit?: number }): Promise<Procedure[]>;
  procedureFeedback(procedureId: string, options?: { success?: boolean; context?: string; failedAtStep?: number }): Promise<FeedbackResult>;
  procedureHistory(procedureId: string, options?: { userId?: string }): Promise<ProcedureHistoryResult>;
  procedureEvolution(procedureId: string, options?: { userId?: string }): Promise<{ evolution: ProcedureEvolutionEntry[] }>;

  // Unified Search
  searchAll(query: string, options?: { limit?: number; graphDepth?: number }): Promise<UnifiedSearchResult>;

  // Agents
  runAgents(options?: { agent?: string; autoFix?: boolean }): Promise<any>;
  agentHistory(options?: { agent?: string; limit?: number }): Promise<any[]>;
  agentStatus(options?: { userId?: string }): Promise<any>;

  // Insights
  insights(options?: { userId?: string }): Promise<any>;
  reflect(options?: { userId?: string }): Promise<any>;
  reflections(options?: { scope?: string; userId?: string }): Promise<any[]>;

  // Webhooks
  listWebhooks(): Promise<Webhook[]>;
  createWebhook(webhook: { url: string; eventTypes: string[]; name?: string; secret?: string }): Promise<any>;
  deleteWebhook(webhookId: number): Promise<boolean>;
  updateWebhook(webhookId: number, updates?: { url?: string; name?: string; eventTypes?: string[]; active?: boolean }): Promise<any>;

  // Teams
  createTeam(name: string, description?: string): Promise<any>;
  joinTeam(inviteCode: string): Promise<any>;
  listTeams(): Promise<any[]>;
  shareMemory(entityName: string, teamId: number, options?: { userId?: string }): Promise<any>;
  unshareMemory(entityName: string, teamId: number, options?: { userId?: string }): Promise<any>;
  teamMembers(teamId: number): Promise<any[]>;
  leaveTeam(teamId: number): Promise<any>;
  deleteTeam(teamId: number): Promise<any>;

  // API Keys
  listKeys(): Promise<ApiKey[]>;
  createKey(name?: string): Promise<{ key: string; name: string }>;
  revokeKey(keyId: string): Promise<any>;
  renameKey(keyId: string, name: string): Promise<ApiKey>;

  // Memory Management
  reindex(options?: { userId?: string }): Promise<any>;
  dedup(options?: { userId?: string }): Promise<any>;
  dedupAll(options?: { userId?: string }): Promise<any>;
  dedupEntity(name: string, options?: { userId?: string }): Promise<{status: string, entity: string, merged: number}>;
  archiveFact(entityName: string, factContent: string, options?: { userId?: string }): Promise<any>;
  merge(sourceName: string, targetName: string, options?: { userId?: string }): Promise<any>;
  mergeUser(options?: { userId?: string }): Promise<{status: string}>;
  feed(options?: { limit?: number; userId?: string }): Promise<any[]>;

  // Jobs
  jobStatus(jobId: string): Promise<JobStatus>;
  waitForJob(jobId: string, options?: { pollInterval?: number; maxWait?: number }): Promise<any>;

  // Smart Triggers (v2.6)
  getTriggers(userId?: string, options?: { includeFired?: boolean; limit?: number }): Promise<SmartTrigger[]>;
  processTriggers(): Promise<{ processed: number; fired: number; errors: number }>;
  dismissTrigger(triggerId: number): Promise<{ status: string; id: number }>;
  detectTriggers(userId: string, options?: { userId?: string }): Promise<any>;

  // Billing
  getBilling(): Promise<BillingInfo>;
  createCheckout(plan: 'pro' | 'business'): Promise<{ checkout_url: string; transaction_id: string }>;
  createPortal(): Promise<{ url: string }>;

  // Import (v2.9)
  importChatgpt(zipPath: string, options?: { chunkSize?: number; onProgress?: (current: number, total: number, title: string) => void }): Promise<ImportResult>;
  importObsidian(vaultPath: string, options?: { chunkChars?: number; onProgress?: (current: number, total: number, title: string) => void }): Promise<ImportResult>;
  importFiles(paths: string[], options?: { chunkChars?: number; onProgress?: (current: number, total: number, title: string) => void }): Promise<ImportResult>;
}

export interface ImportResult {
  conversations_found: number;
  chunks_sent: number;
  entities_created: string[];
  errors: string[];
  duration_seconds: number;
}

export interface SmartTrigger {
  id: number;
  user_id: string;
  trigger_type: 'reminder' | 'contradiction' | 'pattern';
  title: string;
  detail?: string;
  source_type?: 'episode' | 'fact' | 'procedure';
  source_id?: string;
  fire_at?: string;
  fired: boolean;
  fired_at?: string;
  created_at: string;
}

export default MengramClient;
