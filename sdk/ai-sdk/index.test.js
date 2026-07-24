import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mengramTools, retrieveProfile, saveConversation } from './index.js';

function fakeClient(overrides = {}) {
  return {
    search: async () => [
      { entity: 'Ali', type: 'person', score: 0.9, facts: ['prefers dark mode'], knowledge: [], relations: [] },
    ],
    addText: async () => ({ status: 'queued', job_id: 'j1' }),
    add: async () => ({ status: 'queued', job_id: 'j2' }),
    procedures: async () => [
      {
        id: 'p1', name: 'deploy', trigger_condition: 'deploying', version: 3,
        steps: [{ step: 1, action: 'run migrations', detail: 'before push' }],
        entity_names: [], success_count: 11, fail_count: 1, last_used: null, updated_at: null,
        metadata: { preconditions: [{ assumption: 'db reachable' }] },
      },
    ],
    getProfile: async () => ({ user_id: 'u', system_prompt: 'You know Ali.', facts_used: 3, last_updated: null, status: 'ok' }),
    ...overrides,
  };
}

test('mengramTools returns three tools with expected shape', () => {
  const tools = mengramTools({ client: fakeClient() });
  for (const name of ['searchMemory', 'addMemory', 'getProcedures']) {
    assert.ok(tools[name], `missing tool ${name}`);
    assert.ok(tools[name].description, `${name} has description`);
    assert.ok(tools[name].inputSchema, `${name} has inputSchema`);
    assert.equal(typeof tools[name].execute, 'function', `${name} has execute`);
  }
});

test('mengramTools throws without apiKey or client', () => {
  assert.throws(() => mengramTools({}), /apiKey/);
});

test('searchMemory executes and formats results', async () => {
  const tools = mengramTools({ client: fakeClient() });
  const out = await tools.searchMemory.execute({ query: 'preferences' }, { toolCallId: 't', messages: [] });
  assert.equal(out.found, 1);
  assert.equal(out.results[0].entity, 'Ali');
  assert.deepEqual(out.results[0].facts, ['prefers dark mode']);
});

test('addMemory executes', async () => {
  const tools = mengramTools({ client: fakeClient() });
  const out = await tools.addMemory.execute({ content: 'Ali uses Vim' }, { toolCallId: 't', messages: [] });
  assert.equal(out.status, 'queued');
});

test('getProcedures surfaces track record and preconditions', async () => {
  const tools = mengramTools({ client: fakeClient() });
  const out = await tools.getProcedures.execute({ task: 'deploy' }, { toolCallId: 't', messages: [] });
  assert.equal(out.found, 1);
  const p = out.procedures[0];
  assert.equal(p.version, 3);
  assert.deepEqual(p.track_record, { successes: 11, failures: 1 });
  assert.equal(p.preconditions.length, 1);
});

test('getProcedures tolerates procedures without metadata', async () => {
  const client = fakeClient({
    procedures: async () => [{
      id: 'p2', name: 'release', trigger_condition: null, version: 1,
      steps: [], entity_names: [], success_count: 0, fail_count: 0, last_used: null, updated_at: null,
    }],
  });
  const out = await mengramTools({ client }).getProcedures.execute({ task: 'release' }, { toolCallId: 't', messages: [] });
  assert.deepEqual(out.procedures[0].preconditions, []);
});

test('retrieveProfile returns system prompt when ok', async () => {
  assert.equal(await retrieveProfile({ client: fakeClient() }), 'You know Ali.');
});

test('retrieveProfile returns empty string when no data', async () => {
  const client = fakeClient({ getProfile: async () => ({ status: 'no_data', system_prompt: '', user_id: 'u', facts_used: 0, last_updated: null }) });
  assert.equal(await retrieveProfile({ client }), '');
});

test('saveConversation filters non-text and empty messages', async () => {
  let captured;
  const client = fakeClient({ add: async (msgs) => { captured = msgs; return { status: 'queued' }; } });
  await saveConversation([
    { role: 'user', content: 'hi' },
    { role: 'assistant', content: [{ type: 'tool-call' }] },
    { role: 'system', content: 'ignored' },
    { role: 'assistant', content: '  ' },
    { role: 'assistant', content: 'hello' },
  ], { client });
  assert.deepEqual(captured, [
    { role: 'user', content: 'hi' },
    { role: 'assistant', content: 'hello' },
  ]);
});

test('saveConversation skips when nothing to save', async () => {
  const out = await saveConversation([], { client: fakeClient() });
  assert.equal(out.status, 'skipped');
});
