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
// Both functions run together: the refresh hands off to the stats debouncer,
// so exercising the real one keeps the ordering guarantee under test rather
// than stubbing the very thing that enforces it.
const quietSrc = scripts.match(/async function _refreshFeedQuietly\(box\)[\s\S]*?\n\}/)[0]
    + '\nlet _statsAfterFeed = null;\n'
    + scripts.match(/function _refreshStatsAfterFeed\(\)[\s\S]*?\n\}/)[0];

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
    let statsAt = -1;
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
        loadStats: () => { statsLoaded = true; statsAt = requests.length; },
        // Run the debounced callback straight away so the assertion sees the
        // real effect; the marker-cleanup timeout stays a no-op (no delay arg).
        setTimeout: (fn, ms) => { if (ms === 120) fn(); },
        clearTimeout: () => {},
    };
    const keys = Object.keys(sandbox);
    new Function(...keys, `${quietSrc}; return _refreshFeedQuietly(null);`)(...keys.map(k => sandbox[k]));
    return {
        inserted, requests,
        get statsLoaded() { return statsLoaded; },
        get statsAt() { return statsAt; },
    };
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
        clearTimeout: () => {},
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
        clearTimeout: () => {},
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

// Regression: the header counters animate, and the feed refresh starts one on
// every change. Two overlapping runs used to leave two requestAnimationFrame
// loops writing to the same element, each with its own step and target, and
// whichever frame landed last decided the value — so the counter could come to
// rest on a number the server never reported.
function countUpRace({ from, first, second, interrupt }) {
    const src = scripts.match(/let _countUpRun = 0;[\s\S]*?\nfunction countUp\(el, target, duration=600\)[\s\S]*?\n\}/)[0];
    // Record every write so an abandoned run that keeps painting is visible;
    // the final value alone cannot show it, since both runs end on their target.
    const writes = [];
    let _text = String(from);
    const el = {
        dataset: {},
        get textContent() { return _text; },
        set textContent(v) { _text = String(v); writes.push(Number(v)); },
    };
    let queue = [];
    const sandbox = {
        requestAnimationFrame: fn => { queue.push(fn); },
    };
    const keys = Object.keys(sandbox);
    const countUp = new Function(...keys, `${src}; return countUp;`)(...keys.map(k => sandbox[k]));

    countUp(el, first);
    // Let the first animation get partway, mid-flight.
    for (let i = 0; i < interrupt; i++) {
        const pending = queue; queue = [];
        pending.forEach(fn => fn());
    }
    const writesBeforeRetarget = writes.length;
    countUp(el, second);
    // Drain everything still queued, both loops included.
    for (let i = 0; i < 500 && queue.length; i++) {
        const pending = queue; queue = [];
        pending.forEach(fn => fn());
    }
    return { final: Number(el.textContent), after: writes.slice(writesBeforeRetarget) };
}

// Interrupted mid-animation, the newest target must win exactly.
assert.strictEqual(countUpRace({ from: 3900, first: 3991, second: 3989, interrupt: 5 }).final, 3989,
    'a counter interrupted mid-animation must settle on the newest value');
// Same target twice must not drift.
assert.strictEqual(countUpRace({ from: 3900, first: 3989, second: 3989, interrupt: 5 }).final, 3989,
    'a repeated target must settle exactly, not overshoot');
// Downward correction after an upward run: the case seen on screen.
assert.strictEqual(countUpRace({ from: 3989, first: 4050, second: 3991, interrupt: 3 }).final, 3991,
    'a counter must land on the last requested value even when direction flips');
// Interrupted before the first frame ran at all.
assert.strictEqual(countUpRace({ from: 100, first: 900, second: 105, interrupt: 0 }).final, 105,
    'an immediate re-target must not leave the first run animating');

// The final value alone proves nothing: an abandoned run also ends by writing
// its own target, so both implementations land on the same last number. What
// the generation counter actually buys is that the abandoned run stops
// painting — otherwise two tickers alternate on one element and the counter
// visibly jitters between two unrelated values before settling.
//
// After a downward re-target every write must descend. A surviving upward
// ticker interleaves rising values among them, which is exactly the jitter
// seen on screen.
const flip = countUpRace({ from: 3989, first: 4050, second: 3991, interrupt: 3 });
assert(flip.after.every((v, i) => i === 0 || v <= flip.after[i - 1]),
    `writes after a downward re-target must descend monotonically, got ${flip.after}`);

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
    // Regression: /v1/stats used to be fired alongside the feed fetch, so the
    // two responses could cross and the counter settled on a number matching
    // neither the rows on screen nor the database (observed live: 3991 shown,
    // 3989 after a reload). Reading stats only after both feed requests are in
    // keeps the counter at or ahead of the rows, never behind them.
    assert.strictEqual(changed.statsAt, changed.requests.length,
        'stats must be read after the feed responses, not raced against them');
    // Unchanged feed: the cheap probe fires, the full page fetch must not.
    assert.strictEqual(unchanged.requests.length, 1, 'unchanged total costs exactly one probe request');
    assert(unchanged.requests[0].includes('limit=1'), 'probe asks for a single row, not a full page');
    assert.deepStrictEqual(unchanged.inserted, [], 'unchanged feed touches no DOM');
    console.log('dashboard tab refresh: all checks passed');
});
