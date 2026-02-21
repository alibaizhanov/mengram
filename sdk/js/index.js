/**
 * Mengram Cloud SDK for JavaScript / TypeScript
 *
 * Usage:
 *   const { MengramClient } = require('mengram-ai');
 *   // or: import { MengramClient } from 'mengram-ai';
 *
 *   const m = new MengramClient('mg-...');
 *
 *   await m.add([
 *     { role: 'user', content: 'I prefer dark mode and use Vim.' },
 *     { role: 'assistant', content: 'Noted!' }
 *   ], { userId: 'ali' });
 *
 *   const results = await m.search('editor preferences', { userId: 'ali' });
 */

class MengramClient {
  /**
   * @param {string} apiKey - Your Mengram API key (mg-...)
   * @param {object} [options]
   * @param {string} [options.baseUrl] - API base URL (default: https://mengram.io)
   * @param {number} [options.timeout] - Request timeout in ms (default: 30000)
   */
  constructor(apiKey, options = {}) {
    if (!apiKey) throw new Error('API key is required');
    this.apiKey = apiKey;
    this.baseUrl = (options.baseUrl || 'https://mengram.io').replace(/\/$/, '');
    this.timeout = options.timeout || 30000;
  }

  async _request(method, path, body = null, params = null) {
    let url = `${this.baseUrl}${path}`;

    if (params) {
      const qs = Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
        .join('&');
      if (qs) url += `?${qs}`;
    }

    const headers = {
      'Authorization': `Bearer ${this.apiKey}`,
      'Content-Type': 'application/json',
    };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    try {
      const res = await fetch(url, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });

      const data = await res.json();
      if (!res.ok) {
        throw new MengramError(data.detail || `HTTP ${res.status}`, res.status);
      }
      return data;
    } catch (err) {
      if (err instanceof MengramError) throw err;
      if (err.name === 'AbortError') {
        throw new MengramError(`Request timeout after ${this.timeout}ms`, 408);
      }
      throw new MengramError(err.message, 0);
    } finally {
      clearTimeout(timer);
    }
  }

  // ---- Memory ----

  /**
   * Add memories from conversation.
   * @param {Array<{role: string, content: string}>} messages
   * @param {object} [options]
   * @param {string} [options.userId] - User ID (default: 'default')
   * @param {string} [options.agentId] - Agent ID for multi-agent systems
   * @param {string} [options.runId] - Run/session ID
   * @param {string} [options.appId] - Application ID
   * @param {string} [options.expirationDate] - ISO datetime — facts auto-expire after this
   * @returns {Promise<{status: string, job_id?: string}>}
   */
  async add(messages, options = {}) {
    return this._request('POST', '/v1/add', {
      messages,
      user_id: options.userId || 'default',
      agent_id: options.agentId || null,
      run_id: options.runId || null,
      app_id: options.appId || null,
      expiration_date: options.expirationDate || null,
    });
  }

  /**
   * Add memory from plain text.
   * @param {string} text
   * @param {object} [options]
   * @param {string} [options.userId]
   * @param {string} [options.agentId]
   * @param {string} [options.runId]
   * @param {string} [options.appId]
   * @returns {Promise<{status: string}>}
   */
  async addText(text, options = {}) {
    return this._request('POST', '/v1/add_text', {
      text,
      user_id: options.userId || 'default',
      agent_id: options.agentId || null,
      run_id: options.runId || null,
      app_id: options.appId || null,
    });
  }

  /**
   * Semantic search across memories.
   * @param {string} query
   * @param {object} [options]
   * @param {string} [options.userId]
   * @param {string} [options.agentId]
   * @param {string} [options.runId]
   * @param {string} [options.appId]
   * @param {number} [options.limit] - Max results (default: 5)
   * @returns {Promise<Array>}
   */
  async search(query, options = {}) {
    const data = await this._request('POST', '/v1/search', {
      query,
      user_id: options.userId || 'default',
      agent_id: options.agentId || null,
      run_id: options.runId || null,
      app_id: options.appId || null,
      limit: options.limit || 5,
    });
    return data.results || [];
  }

  /**
   * Get all memories.
   * @param {object} [options]
   * @param {string} [options.userId]
   * @param {string} [options.agentId]
   * @param {string} [options.appId]
   * @returns {Promise<Array>}
   */
  async getAll(options = {}) {
    const params = {};
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    if (options.agentId) params.agent_id = options.agentId;
    if (options.appId) params.app_id = options.appId;
    const data = await this._request('GET', '/v1/memories', null, params);
    return data.memories || [];
  }

  /**
   * Get all memories with full details.
   * @returns {Promise<Array>}
   */
  async getAllFull(options = {}) {
    const params = {};
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    const data = await this._request('GET', '/v1/memories/full', null, params);
    return data.memories || [];
  }

  /**
   * Get specific entity.
   * @param {string} name - Entity name
   * @returns {Promise<object|null>}
   */
  async get(name, options = {}) {
    try {
      const params = {};
      if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
      return await this._request('GET', `/v1/memory/${encodeURIComponent(name)}`, null, params);
    } catch {
      return null;
    }
  }

  /**
   * Delete a memory entity.
   * @param {string} name
   * @returns {Promise<boolean>}
   */
  async delete(name, options = {}) {
    try {
      const params = {};
      if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
      await this._request('DELETE', `/v1/entity/${encodeURIComponent(name)}`, null, params);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Get usage statistics.
   * @returns {Promise<object>}
   */
  async stats(options = {}) {
    const params = {};
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    return this._request('GET', '/v1/stats', null, params);
  }

  /**
   * Get knowledge graph.
   * @returns {Promise<{nodes: Array, edges: Array}>}
   */
  async graph(options = {}) {
    const params = {};
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    return this._request('GET', '/v1/graph', null, params);
  }

  // ---- Cognitive Profile ----

  /**
   * Generate a Cognitive Profile — a ready-to-use system prompt from user memory.
   * @param {string} userId - User to generate profile for
   * @param {object} [options]
   * @param {boolean} [options.force] - Regenerate even if cached
   * @returns {Promise<{user_id: string, system_prompt: string, facts_used: number, status: string}>}
   */
  async getProfile(userId = 'default', options = {}) {
    const params = {};
    if (options.force) params.force = 'true';
    if (userId && userId !== 'default') params.sub_user_id = userId;
    return this._request('GET', '/v1/profile', null, params);
  }

  // ---- Episodic Memory ----

  /**
   * Get or search episodic memories (events, interactions, experiences).
   * @param {object} [options]
   * @param {string} [options.query] - Search query (if omitted, returns recent episodes)
   * @param {number} [options.limit] - Max results (default 20)
   * @param {string} [options.after] - ISO datetime filter start
   * @param {string} [options.before] - ISO datetime filter end
   * @returns {Promise<Array>}
   */
  async episodes(options = {}) {
    if (options.query) {
      const params = { query: options.query, limit: options.limit || 5 };
      if (options.after) params.after = options.after;
      if (options.before) params.before = options.before;
      if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
      const data = await this._request('GET', '/v1/episodes/search', null, params);
      return data.results || [];
    } else {
      const params = { limit: options.limit || 20 };
      if (options.after) params.after = options.after;
      if (options.before) params.before = options.before;
      if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
      const data = await this._request('GET', '/v1/episodes', null, params);
      return data.episodes || [];
    }
  }

  // ---- Procedural Memory ----

  /**
   * Get or search procedural memories (learned workflows, skills).
   * @param {object} [options]
   * @param {string} [options.query] - Search query (if omitted, returns all procedures)
   * @param {number} [options.limit] - Max results (default 20)
   * @returns {Promise<Array>}
   */
  async procedures(options = {}) {
    if (options.query) {
      const params = { query: options.query, limit: options.limit || 5 };
      if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
      const data = await this._request('GET', '/v1/procedures/search', null, params);
      return data.results || [];
    } else {
      const params = { limit: options.limit || 20 };
      if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
      const data = await this._request('GET', '/v1/procedures', null, params);
      return data.procedures || [];
    }
  }

  /**
   * Record success/failure feedback for a procedure.
   * On failure with context, triggers experience-driven evolution.
   * @param {string} procedureId - UUID of the procedure
   * @param {object} [options]
   * @param {boolean} [options.success] - true if worked, false if failed (default true)
   * @param {string} [options.context] - What went wrong (triggers evolution on failure)
   * @param {number} [options.failedAtStep] - Which step number failed
   * @returns {Promise<object>}
   */
  async procedureFeedback(procedureId, options = {}) {
    const success = options.success !== false;
    const body = options.context ? {
      context: options.context,
      ...(options.failedAtStep != null && { failed_at_step: options.failedAtStep }),
    } : null;
    const params = { success: success ? 'true' : 'false' };
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    return this._request('PATCH', `/v1/procedures/${procedureId}/feedback`, body, params);
  }

  /**
   * Get version history for a procedure.
   * @param {string} procedureId - UUID of any version of the procedure
   * @returns {Promise<{versions: Array, evolution_log: Array}>}
   */
  async procedureHistory(procedureId, options = {}) {
    const params = {};
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    return this._request('GET', `/v1/procedures/${procedureId}/history`, null, params);
  }

  /**
   * Get the evolution log for a procedure.
   * @param {string} procedureId - UUID of any version of the procedure
   * @returns {Promise<{evolution: Array}>}
   */
  async procedureEvolution(procedureId, options = {}) {
    const params = {};
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    return this._request('GET', `/v1/procedures/${procedureId}/evolution`, null, params);
  }

  // ---- Unified Search ----

  /**
   * Search across all 3 memory types: semantic, episodic, procedural.
   * @param {string} query - Search query
   * @param {object} [options]
   * @param {number} [options.limit] - Max results per type (default 5)
   * @returns {Promise<{semantic: Array, episodic: Array, procedural: Array}>}
   */
  async searchAll(query, options = {}) {
    return this._request('POST', '/v1/search/all', {
      query,
      limit: options.limit || 5,
      user_id: options.userId || 'default',
    });
  }

  /**
   * Timeline search.
   * @param {object} [options]
   * @param {string} [options.after] - ISO date
   * @param {string} [options.before] - ISO date
   * @param {number} [options.limit]
   * @returns {Promise<Array>}
   */
  async timeline(options = {}) {
    const params = { limit: options.limit || 20 };
    if (options.after) params.after = options.after;
    if (options.before) params.before = options.before;
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    const data = await this._request('GET', '/v1/timeline', null, params);
    return data.results || [];
  }

  // ---- Agents ----

  /**
   * Run memory agents.
   * @param {object} [options]
   * @param {string} [options.agent] - 'curator', 'connector', 'digest', or 'all'
   * @param {boolean} [options.autoFix] - Auto-archive bad facts
   * @returns {Promise<object>}
   */
  async runAgents(options = {}) {
    const params = {
      agent: options.agent || 'all',
      auto_fix: options.autoFix ? 'true' : 'false',
    };
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    return this._request('POST', '/v1/agents/run', null, params);
  }

  /**
   * Get agent run history.
   * @param {object} [options]
   * @param {string} [options.agent]
   * @param {number} [options.limit]
   * @returns {Promise<Array>}
   */
  async agentHistory(options = {}) {
    const params = { limit: options.limit || 10 };
    if (options.agent) params.agent = options.agent;
    const data = await this._request('GET', '/v1/agents/history', null, params);
    return data.runs || [];
  }

  // ---- Insights ----

  /**
   * Get AI insights and reflections.
   * @returns {Promise<object>}
   */
  async insights(options = {}) {
    const params = {};
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    return this._request('GET', '/v1/insights', null, params);
  }

  /**
   * Trigger reflection generation.
   * @returns {Promise<object>}
   */
  async reflect(options = {}) {
    const params = {};
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    return this._request('POST', '/v1/reflect', null, params);
  }

  // ---- Webhooks ----

  /**
   * List webhooks.
   * @returns {Promise<Array>}
   */
  async listWebhooks() {
    const data = await this._request('GET', '/v1/webhooks');
    return data.webhooks || [];
  }

  /**
   * Create webhook.
   * @param {object} webhook
   * @param {string} webhook.url
   * @param {string[]} webhook.eventTypes
   * @param {string} [webhook.name]
   * @param {string} [webhook.secret]
   * @returns {Promise<object>}
   */
  async createWebhook(webhook) {
    return this._request('POST', '/v1/webhooks', {
      url: webhook.url,
      event_types: webhook.eventTypes,
      name: webhook.name || '',
      secret: webhook.secret || '',
    });
  }

  /**
   * Delete webhook.
   * @param {number} webhookId
   * @returns {Promise<boolean>}
   */
  async deleteWebhook(webhookId) {
    try {
      await this._request('DELETE', `/v1/webhooks/${webhookId}`);
      return true;
    } catch {
      return false;
    }
  }

  // ---- Teams ----

  /**
   * Create a team.
   * @param {string} name
   * @param {string} [description]
   * @returns {Promise<object>}
   */
  async createTeam(name, description = '') {
    return this._request('POST', '/v1/teams', { name, description });
  }

  /**
   * Join a team with invite code.
   * @param {string} inviteCode
   * @returns {Promise<object>}
   */
  async joinTeam(inviteCode) {
    return this._request('POST', '/v1/teams/join', { invite_code: inviteCode });
  }

  /**
   * List your teams.
   * @returns {Promise<Array>}
   */
  async listTeams() {
    const data = await this._request('GET', '/v1/teams');
    return data.teams || [];
  }

  /**
   * Share memory with a team.
   * @param {string} entityName
   * @param {number} teamId
   * @returns {Promise<object>}
   */
  async shareMemory(entityName, teamId, options = {}) {
    const params = {};
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    return this._request('POST', `/v1/teams/${teamId}/share`, { entity: entityName }, params);
  }

  // ---- API Keys ----

  /**
   * List API keys.
   * @returns {Promise<Array>}
   */
  async listKeys() {
    const data = await this._request('GET', '/v1/keys');
    return data.keys || [];
  }

  /**
   * Create a new API key.
   * @param {string} [name] - Key name
   * @returns {Promise<{key: string, name: string}>}
   */
  async createKey(name = 'default') {
    return this._request('POST', '/v1/keys', { name });
  }

  /**
   * Revoke an API key.
   * @param {number} keyId
   * @returns {Promise<object>}
   */
  async revokeKey(keyId) {
    return this._request('DELETE', `/v1/keys/${keyId}`);
  }

  // ---- Jobs (Async) ----

  /**
   * Check status of a background job.
   * @param {string} jobId
   * @returns {Promise<{status: string, result?: object}>}
   */
  async jobStatus(jobId) {
    return this._request('GET', `/v1/jobs/${jobId}`);
  }

  /**
   * Wait for a job to complete.
   * @param {string} jobId
   * @param {object} [options]
   * @param {number} [options.pollInterval] - ms between polls (default: 1000)
   * @param {number} [options.maxWait] - max ms to wait (default: 60000)
   * @returns {Promise<object>}
   */
  async waitForJob(jobId, options = {}) {
    const interval = options.pollInterval || 1000;
    const maxWait = options.maxWait || 60000;
    const start = Date.now();

    while (Date.now() - start < maxWait) {
      const job = await this.jobStatus(jobId);
      if (job.status === 'completed' || job.status === 'failed') {
        return job;
      }
      await new Promise(r => setTimeout(r, interval));
    }
    throw new MengramError('Job timed out', 408);
  }

  // ---- Smart Triggers (v2.6) ----

  /**
   * Get smart triggers (reminders, contradictions, patterns).
   * @param {string} [userId] - defaults to 'default'
   * @param {object} [options]
   * @param {boolean} [options.includeFired] - include already-fired triggers
   * @param {number} [options.limit] - max triggers to return
   * @returns {Promise<Array>}
   */
  async getTriggers(userId = null, options = {}) {
    const params = {};
    if (options.includeFired) params.include_fired = 'true';
    if (options.limit) params.limit = options.limit;
    if (options.userId && options.userId !== 'default') params.sub_user_id = options.userId;
    const path = userId ? `/v1/triggers/${userId}` : '/v1/triggers';
    const result = await this._request('GET', path, null, params);
    return result.triggers || [];
  }

  /**
   * Manually process all pending triggers.
   * @returns {Promise<object>}
   */
  async processTriggers() {
    return this._request('POST', '/v1/triggers/process');
  }

  /**
   * Dismiss a trigger without sending webhook.
   * @param {number} triggerId
   * @returns {Promise<object>}
   */
  async dismissTrigger(triggerId) {
    return this._request('DELETE', `/v1/triggers/${triggerId}`);
  }

  // ---- Import ----

  /**
   * Import ChatGPT export ZIP into memory.
   * Node.js only — reads file from disk.
   * @param {string} zipPath - Path to ChatGPT export ZIP file
   * @param {object} [options]
   * @param {number} [options.chunkSize] - Max messages per chunk (default 20)
   * @param {function} [options.onProgress] - Callback(current, total, title)
   * @returns {Promise<{conversations_found: number, chunks_sent: number, entities_created: string[], errors: string[], duration_seconds: number}>}
   */
  async importChatgpt(zipPath, options = {}) {
    const fs = await import('fs');
    const path = await import('path');
    const { default: JSZip } = await import('jszip').catch(() => {
      throw new MengramError(
        'jszip is required for ChatGPT import: npm install jszip', 0
      );
    });

    const start = Date.now();
    const result = { conversations_found: 0, chunks_sent: 0, entities_created: [], errors: [], duration_seconds: 0 };
    const chunkSize = options.chunkSize || 20;

    try {
      const data = fs.readFileSync(zipPath);
      const zip = await JSZip.loadAsync(data);
      const convFile = Object.keys(zip.files).find(n => n.endsWith('conversations.json'));
      if (!convFile) throw new Error('No conversations.json found in ZIP');

      const convData = JSON.parse(await zip.files[convFile].async('string'));
      if (!Array.isArray(convData)) throw new Error('conversations.json should contain a list');

      // Parse conversations from tree structure
      const conversations = [];
      for (const conv of convData) {
        const mapping = conv.mapping || {};
        const messages = this._walkChatgptTree(mapping);
        if (messages.length > 0) conversations.push(messages);
      }

      result.conversations_found = conversations.length;

      let chunkIdx = 0;
      const totalChunks = conversations.reduce((sum, conv) =>
        sum + Math.ceil(conv.length / chunkSize), 0);

      for (let i = 0; i < conversations.length; i++) {
        const conv = conversations[i];
        for (let j = 0; j < conv.length; j += chunkSize) {
          const chunk = conv.slice(j, j + chunkSize);
          try {
            const addOpts = {};
            if (options.userId) addOpts.userId = options.userId;
            await this.add(chunk, addOpts);
            result.chunks_sent++;
            chunkIdx++;
            if (options.onProgress) {
              options.onProgress(chunkIdx, totalChunks, `conversation ${i + 1}/${conversations.length}`);
            }
          } catch (e) {
            result.errors.push(`Conversation ${i + 1}: ${e.message}`);
          }
        }
      }
    } catch (e) {
      if (!result.errors.length) result.errors.push(e.message);
    }

    result.duration_seconds = (Date.now() - start) / 1000;
    return result;
  }

  /**
   * Import Obsidian vault into memory.
   * Node.js only — reads files from disk.
   * @param {string} vaultPath - Path to Obsidian vault directory
   * @param {object} [options]
   * @param {number} [options.chunkChars] - Max characters per chunk (default 4000)
   * @param {function} [options.onProgress] - Callback(current, total, title)
   * @returns {Promise<{conversations_found: number, chunks_sent: number, entities_created: string[], errors: string[], duration_seconds: number}>}
   */
  async importObsidian(vaultPath, options = {}) {
    const fs = await import('fs');
    const path = await import('path');

    const start = Date.now();
    const result = { conversations_found: 0, chunks_sent: 0, entities_created: [], errors: [], duration_seconds: 0 };
    const chunkChars = options.chunkChars || 4000;

    const mdFiles = this._findMdFiles(fs, path, vaultPath);
    result.conversations_found = mdFiles.length;

    const fileChunks = mdFiles.map(f => {
      try {
        const content = fs.readFileSync(f, 'utf-8');
        return { file: f, chunks: this._chunkText(content, chunkChars) };
      } catch {
        return { file: f, chunks: [] };
      }
    });

    const totalChunks = fileChunks.reduce((sum, fc) => sum + Math.max(fc.chunks.length, 1), 0);
    let chunkIdx = 0;

    for (const { file, chunks } of fileChunks) {
      const title = path.basename(file, '.md');
      if (!chunks.length) { chunkIdx++; continue; }

      for (const chunk of chunks) {
        try {
          const addOpts = {};
          if (options.userId) addOpts.userId = options.userId;
          await this.add([{ role: 'user', content: `Note: ${title}\n\n${chunk}` }], addOpts);
          result.chunks_sent++;
          chunkIdx++;
          if (options.onProgress) options.onProgress(chunkIdx, totalChunks, title);
        } catch (e) {
          result.errors.push(`${title}: ${e.message}`);
          chunkIdx++;
        }
      }
    }

    result.duration_seconds = (Date.now() - start) / 1000;
    return result;
  }

  /**
   * Import text/markdown files into memory.
   * Node.js only — reads files from disk.
   * @param {string[]} paths - File paths
   * @param {object} [options]
   * @param {number} [options.chunkChars] - Max characters per chunk (default 4000)
   * @param {function} [options.onProgress] - Callback(current, total, title)
   * @returns {Promise<{conversations_found: number, chunks_sent: number, entities_created: string[], errors: string[], duration_seconds: number}>}
   */
  async importFiles(paths, options = {}) {
    const fs = await import('fs');
    const path = await import('path');

    const start = Date.now();
    const result = { conversations_found: 0, chunks_sent: 0, entities_created: [], errors: [], duration_seconds: 0 };
    const chunkChars = options.chunkChars || 4000;

    // Resolve paths — expand directories
    const resolved = [];
    for (const p of paths) {
      try {
        const stat = fs.statSync(p);
        if (stat.isFile()) {
          resolved.push(p);
        } else if (stat.isDirectory()) {
          const files = this._findMdFiles(fs, path, p);
          // Also include .txt files
          const txtFiles = fs.readdirSync(p, { recursive: true })
            .filter(f => f.endsWith('.txt'))
            .map(f => path.join(p, f));
          resolved.push(...files, ...txtFiles);
        }
      } catch { /* skip missing */ }
    }

    result.conversations_found = resolved.length;

    const fileChunks = resolved.map(f => {
      try {
        const content = fs.readFileSync(f, 'utf-8');
        return { file: f, chunks: this._chunkText(content, chunkChars) };
      } catch {
        return { file: f, chunks: [] };
      }
    });

    const totalChunks = fileChunks.reduce((sum, fc) => sum + Math.max(fc.chunks.length, 1), 0);
    let chunkIdx = 0;

    for (const { file, chunks } of fileChunks) {
      const title = path.basename(file, path.extname(file));
      if (!chunks.length) { chunkIdx++; continue; }

      for (const chunk of chunks) {
        try {
          const addOpts = {};
          if (options.userId) addOpts.userId = options.userId;
          await this.add([{ role: 'user', content: `Note: ${title}\n\n${chunk}` }], addOpts);
          result.chunks_sent++;
          chunkIdx++;
          if (options.onProgress) options.onProgress(chunkIdx, totalChunks, title);
        } catch (e) {
          result.errors.push(`${title}: ${e.message}`);
          chunkIdx++;
        }
      }
    }

    result.duration_seconds = (Date.now() - start) / 1000;
    return result;
  }

  // ---- Internal helpers for import ----

  /** Walk ChatGPT's tree-structured mapping to extract ordered messages. */
  _walkChatgptTree(mapping) {
    if (!mapping || !Object.keys(mapping).length) return [];
    let rootId = null;
    for (const [id, node] of Object.entries(mapping)) {
      if (!node.parent) { rootId = id; break; }
    }
    if (!rootId) return [];

    const messages = [];
    let currentId = rootId;
    while (currentId) {
      const node = mapping[currentId];
      if (!node) break;
      const msg = node.message;
      if (msg && msg.content) {
        const role = (msg.author || {}).role || '';
        const contentData = msg.content;
        let text = '';
        if (typeof contentData === 'string') {
          text = contentData;
        } else if (contentData.parts) {
          text = contentData.parts
            .map(p => typeof p === 'string' ? p : (p && p.text) || '')
            .join('');
        }
        text = text.trim();
        if (text && (role === 'user' || role === 'assistant')) {
          messages.push({ role, content: text });
        }
      }
      const children = node.children || [];
      currentId = children[0] || null;
    }
    return messages;
  }

  /** Find .md files recursively, skipping dotfiles and .obsidian/. */
  _findMdFiles(fs, path, dir) {
    const results = [];
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue;
      if (entry.name === 'node_modules' || entry.name === '__pycache__') continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        results.push(...this._findMdFiles(fs, path, full));
      } else if (entry.name.endsWith('.md')) {
        results.push(full);
      }
    }
    return results.sort();
  }

  /** Split text into chunks at paragraph boundaries. */
  _chunkText(text, chunkChars) {
    text = (text || '').trim();
    if (!text) return [];
    if (text.length <= chunkChars) return [text];

    const paragraphs = text.split('\n\n');
    const chunks = [];
    let current = '';

    for (const para of paragraphs) {
      const p = para.trim();
      if (!p) continue;
      if (current.length + p.length + 2 > chunkChars && current) {
        chunks.push(current.trim());
        current = '';
      }
      current += p + '\n\n';
    }
    if (current.trim()) chunks.push(current.trim());
    return chunks;
  }
}

class MengramError extends Error {
  constructor(message, statusCode) {
    super(message);
    this.name = 'MengramError';
    this.statusCode = statusCode;
  }
}

// Export for both CommonJS and ESM
module.exports = { MengramClient, MengramError };
module.exports.default = MengramClient;
