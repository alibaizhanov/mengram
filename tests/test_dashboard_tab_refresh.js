// Regression: switching to the Feed tab must reload it. Before this test's fix,
// loadFeed() ran only once at login, so the feed was stale until a page reload.
// Runs the real switchTab/switchTabDirect source against a stub DOM — no deps.
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, '../cloud/dashboard.html'), 'utf8');
const scripts = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)]
    .map(m => m[1]).join('\n');

function clickTab(tab) {
    const src = scripts.match(/function switchTabDirect\(t\)[\s\S]*?\n\}/)[0]
        + '\n' + scripts.match(/function switchTab\(e, t\)[\s\S]*?\n\}/)[0];
    const calls = [];
    const stub = () => ({
        classList: { remove() {}, add() {}, toggle() {}, contains: () => false },
        style: {}, dataset: {}, querySelector: () => null, querySelectorAll: () => [],
    });
    const sandbox = {
        document: {
            getElementById: () => stub(),
            querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
        },
        history: { replaceState() {} },
        window: { location: 'https://example/?tab=graph' },
        URL: class { constructor() { this.searchParams = { set() {} }; } },
        localStorage: { getItem: () => null, setItem() {} },
        TAB_GROUPS: { memory: { tabs: ['graph', 'search', 'feed', 'entities'] } },
        _updateNavHighlighting() {}, _isCached: () => true,
        _syncFeedAutoRefresh() {}, toggleMobileMenu() {}, _feedAutoRefreshOn: () => false,
    };
    for (const fn of ['loadGraph', 'loadEntities', 'loadFeed', 'loadIntelligence', 'loadInsights',
        'loadAgentHistory', 'loadProcedures', 'loadBilling', 'loadWebhooks', 'loadTeams',
        'loadKeys', 'loadCapturePolicy', 'loadStats']) sandbox[fn] = () => { calls.push(fn); };
    const keys = Object.keys(sandbox);
    new Function(...keys, `${src}; switchTab(null, ${JSON.stringify(tab)});`)(...keys.map(k => sandbox[k]));
    return calls;
}

assert(clickTab('feed').includes('loadFeed'), 'Feed tab must reload feed data on switch');
// The stats strip sits above the Memory tabs and was stale for the same reason.
assert(clickTab('feed').includes('loadStats'), 'Memory tabs must refresh the stats strip');
assert(!clickTab('billing').includes('loadStats'), 'stats strip is hidden outside Memory, do not refetch');
assert(clickTab('insights').includes('loadInsights'), 'Insights tab must still load');
assert(!clickTab('graph').includes('loadGraph'), 'Graph stays cached within its TTL');

// switchTab must stay a thin wrapper — a second dispatch block is how feed
// silently fell out of sync in the first place.
assert.strictEqual((scripts.match(/loadCapturePolicy\(\)/g) || []).length, 2,
    'tab-load dispatch should exist in exactly one place (plus its definition)');

// Auto-refresh is self-hosted-only by default; the maintainer can enable it
// for hosted plans by flipping one constant.
function autoRefreshGate(plan, stored, allPlans) {
    const src = scripts.match(/const FEED_REFRESH_ALL_PLANS[\s\S]*?function _feedAutoRefreshOn\(\)[^\n]*\n/)[0]
        .replace(/const FEED_REFRESH_ALL_PLANS = false;/, `const FEED_REFRESH_ALL_PLANS = ${allPlans};`)
        .replace(/let _plan = '';/, `let _plan = ${JSON.stringify(plan)};`);
    return new Function('localStorage',
        `${src}; return _feedAutoRefreshOn();`)({ getItem: () => stored });
}

assert(autoRefreshGate('selfhosted', '1', false), 'self-hosted + enabled => on');
assert(!autoRefreshGate('selfhosted', null, false), 'self-hosted + not enabled => off');
assert(!autoRefreshGate('pro', '1', false), 'hosted plan must not poll even if localStorage says on');
assert(autoRefreshGate('pro', '1', true), 'maintainer can opt hosted plans in');

// A background refresh must not blank the list to a skeleton, must insert only
// rows that aren't on screen, must place them under the date divider, and must
// do nothing at all when the backend total is unchanged.
const quietSrc = scripts.match(/async function _refreshFeedQuietly\(box\)[\s\S]*?\n\}/)[0];

function runQuietRefresh({ total, feedTotal }) {
    const divider = { __id: 'DIVIDER' };
    const sibling = { __id: 'first-row' };
    const inserted = [];
    const requests = [];
    const list = {
        querySelectorAll: () => ['a', 'b'].map(id => ({ dataset: { factId: id }, classList: { remove() {} } })),
        querySelector: sel => (sel === '.feed-date-divider' ? divider : null),
        insertBefore: (el, ref) => inserted.push([el.__id, ref === sibling ? 'after-divider' : 'TOP', el.style.__delay]),
        firstChild: divider,
    };
    divider.nextSibling = sibling;
    let statsLoaded = false;
    const sandbox = {
        API: '', H: () => ({}), FEED_PAGE: 30,
        _feedOffset: 0, _feedTotal: feedTotal,
        fetch: async (url) => {
            requests.push(url);
            return { json: async () => ({ feed: [{ id: 'c' }, { id: 'b' }, { id: 'a' }], total }) };
        },
        document: {
            getElementById: id => (id === 'feed-list' ? list : null),
            createElement: () => ({
                set innerHTML(v) {
                    this.firstElementChild = {
                        __id: v,
                        classList: { add() {} },
                        style: { setProperty(k, val) { this.__delay = val; }, removeProperty() {} },
                    };
                },
            }),
        },
        _renderFeedItem: it => it.id,
        loadStats: () => { statsLoaded = true; },
        setTimeout: () => {},
    };
    const keys = Object.keys(sandbox);
    new Function(...keys, `${quietSrc}; return _refreshFeedQuietly(null);`)(...keys.map(k => sandbox[k]));
    return { inserted, requests, get statsLoaded() { return statsLoaded; } };
}

// Same harness, but nothing on screen matches — so all three rows are new.
function runQuietRefreshBatch() {
    const inserted = [];
    const divider = { __id: 'DIVIDER' };
    const sibling = { __id: 'first-row' };
    divider.nextSibling = sibling;
    const list = {
        querySelectorAll: () => [],
        querySelector: () => divider,
        insertBefore: (el, ref) => inserted.push([el.__id, ref === sibling ? 'after-divider' : 'TOP', el.style.__delay]),
        firstChild: divider,
    };
    const sandbox = {
        API: '', H: () => ({}), FEED_PAGE: 30, _feedOffset: 0, _feedTotal: 0,
        fetch: async () => ({ json: async () => ({ feed: [{ id: 'x' }, { id: 'y' }, { id: 'z' }], total: 3 }) }),
        document: {
            getElementById: id => (id === 'feed-list' ? list : null),
            createElement: () => ({
                set innerHTML(v) {
                    this.firstElementChild = {
                        __id: v,
                        classList: { add() {} },
                        style: { setProperty(k, val) { this.__delay = val; }, removeProperty() {} },
                    };
                },
            }),
        },
        _renderFeedItem: it => it.id,
        loadStats: () => {},
        setTimeout: () => {},
    };
    const keys = Object.keys(sandbox);
    new Function(...keys, `${quietSrc}; return _refreshFeedQuietly(null);`)(...keys.map(k => sandbox[k]));
    return { inserted };
}

// Regression: the feed froze until a manual reload because _feedTotal was
// recorded before the rows were on screen. Any bail-out after that point left
// the probe believing it was up to date.
{
    const bodyAfterProbe = quietSrc.slice(quietSrc.indexOf('let d;'));
    const insertLoop = bodyAfterProbe.indexOf('list.insertBefore');
    // The only assignment before the insert loop is the no-new-rows early
    // return, where recording the total is correct; the insert path must
    // record it only afterwards.
    assert(bodyAfterProbe.lastIndexOf('_feedTotal = d.total') > insertLoop,
        '_feedTotal must only be recorded after the rows are inserted');
    const beforeLoop = bodyAfterProbe.slice(0, insertLoop);
    assert(!/_feedTotal = d\.total;\s*$/.test(beforeLoop.trimEnd()),
        'no unconditional total update on the insert path before rows land');
    // A probe that throws must fall through to the full fetch, not return.
    assert(/catch \(e\) \{ \/\* fall through \*\/ \}/.test(quietSrc),
        'a failed probe must not be treated as "nothing changed"');
}

// Regression: bursts larger than one page silently lost rows. The probe knows
// how many facts appeared, so the refetch must ask for at least that many —
// otherwise the overflow is never rendered and never seen as "new" again.
{
    const requested = [];
    const list = {
        querySelectorAll: () => [],
        querySelector: () => null,
        insertBefore: () => {},
        firstChild: null,
    };
    const sandbox = {
        API: '', H: () => ({}), FEED_PAGE: 30,
        _feedOffset: 0, _feedTotal: 1000,
        fetch: async (url) => {
            requested.push(url);
            // probe answers first, then the full page
            return { json: async () => ({ feed: [{ id: 'n' }], total: 1042 }) };
        },
        document: {
            getElementById: id => (id === 'feed-list' ? list : null),
            createElement: () => ({
                set innerHTML(v) {
                    this.firstElementChild = {
                        __id: v, classList: { add() {} },
                        style: { setProperty() {}, removeProperty() {} },
                    };
                },
            }),
        },
        _renderFeedItem: it => it.id,
        loadStats: () => {},
        setTimeout: () => {},
    };
    const keys = Object.keys(sandbox);
    new Function(...keys, `${quietSrc}; return _refreshFeedQuietly(null);`)(...keys.map(k => sandbox[k]));
    setImmediate(() => {
        const full = requested.find(u => !u.includes('limit=1&'));
        const limit = Number(/limit=(\d+)/.exec(full)[1]);
        assert(limit >= 42, `42 new facts must be fetched in full, asked for ${limit}`);
    });
}

assert(!/renderSkeleton/.test(quietSrc), 'quiet refresh must never blank the list to a skeleton');

const changed = runQuietRefresh({ total: 3, feedTotal: 2 });
const unchanged = runQuietRefresh({ total: 3, feedTotal: 3 });

setImmediate(() => {
    assert.deepStrictEqual(changed.inserted, [['c', 'after-divider', '0.00s']],
        'only new facts, inserted below the date divider, top row animating first');
    // A batch must cascade: each row further down starts later, so the list
    // does not lurch all at once.
    const batch = runQuietRefreshBatch();
    setImmediate(() => {
        // Response order is newest-first and must be preserved on screen: the
        // batch is inserted front-to-back against a fixed anchor. Inserting a
        // reversed list against that same anchor flipped the feed (observed
        // live: an older row ended up above a newer one).
        assert.deepStrictEqual(batch.inserted.map(r => r[0]), ['x', 'y', 'z'],
            'rows keep the response order, newest first');
        assert.deepStrictEqual(batch.inserted.map(r => r[2]), ['0.00s', '0.18s', '0.36s'],
            'newest row animates first, each later row delayed further');
    });
    assert(changed.statsLoaded, 'stats strip refreshes when the feed changed');
    // Unchanged feed: the cheap probe fires, the full page fetch must not.
    assert.strictEqual(unchanged.requests.length, 1, 'unchanged total costs exactly one probe request');
    assert(unchanged.requests[0].includes('limit=1'), 'probe asks for a single row, not a full page');
    assert.deepStrictEqual(unchanged.inserted, [], 'unchanged feed touches no DOM');
    console.log('dashboard tab refresh: all checks passed');
});
