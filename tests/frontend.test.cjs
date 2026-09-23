const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const BriefingStorage = require('../site/storage.js');
const BriefingRefresh = require('../site/refresh.js');

const FIX = (...p) => path.join(__dirname, 'fixtures', 'contracts', ...p);
const load = name => JSON.parse(fs.readFileSync(FIX(name), 'utf8'));

const lifecycle = load('refresh-job-lifecycle.json');
const noChange = load('no-change-result.json');
const degraded = load('degraded-edition.json');
const archived = load('archived-edition.json');
const manualRequest = lifecycle.request_id;

test('requestAcknowledged requires exact request id in live edition', () => {
  assert.equal(BriefingRefresh.requestAcknowledged(noChange, manualRequest), true);
  assert.equal(BriefingRefresh.requestAcknowledged(noChange, 'req_missing'), false);
  assert.equal(BriefingRefresh.requestAcknowledged(archived, manualRequest), false);
});

test('unrelated newer edition is detected without request match', () => {
  const baseline = { generated_at: '2026-09-23T11:01:17.817774+00:00', request_ids: ['req_old'] };
  const newer = { generated_at: '2026-09-23T17:05:00.000000+00:00', request_ids: ['req_other'] };
  assert.equal(BriefingRefresh.unrelatedNewerEdition(newer, manualRequest, baseline), true);
  assert.equal(BriefingRefresh.unrelatedNewerEdition(noChange, manualRequest, baseline), false);
});

test('job status messages stay user-facing', () => {
  assert.match(BriefingRefresh.userStatusMessage('building'), /Building/i);
  assert.match(BriefingRefresh.userStatusMessage('rate_limited', { retry_after: '2026-09-23T17:16:00.000Z' }), /wait/i);
  assert.equal(BriefingRefresh.isTerminalJobStatus('failed'), true);
  assert.equal(BriefingRefresh.isActiveJobStatus('publishing'), true);
});

test('poll backoff grows and slows when tab hidden', () => {
  assert.equal(BriefingRefresh.pollDelayMs(0, true), 2000);
  assert.ok(BriefingRefresh.pollDelayMs(2, true) < BriefingRefresh.pollDelayMs(6, true));
  assert.ok(BriefingRefresh.pollDelayMs(1, false) >= BriefingRefresh.pollDelayMs(1, true));
});

test('parseReturnParams reads owner redirect query', () => {
  const params = BriefingRefresh.parseReturnParams('?request=req_abc&job=job_abc&status=/refresh/job_abc');
  assert.equal(params.requestId, 'req_abc');
  assert.equal(params.jobId, 'job_abc');
  assert.equal(params.statusPath, '/refresh/job_abc');
});

test('buildOwnerUrl includes return link', () => {
  const href = BriefingRefresh.buildOwnerUrl('https://worker.example/owner', 'https://pages.example/News/');
  assert.match(href, /return=/);
  assert.match(href, /owner/);
});

test('sectionAgeNote covers stale and unchanged sections', () => {
  const sport = degraded.sections.sport;
  const note = BriefingRefresh.sectionAgeNote(sport, degraded);
  assert.match(note, /Carried forward|updated/i);
  const unchanged = BriefingRefresh.sectionAgeNote(noChange.sections.news, noChange);
  assert.match(unchanged, /Unchanged/i);
});

test('financeCaption distinguishes quote and briefing times', () => {
  const caption = BriefingRefresh.financeCaption(
    { quote_checked_at: '2026-09-23T17:04:00.000Z' },
    { generated_at: '2026-09-23T17:05:00.000Z' },
  );
  assert.match(caption, /Quotes/i);
  assert.match(caption, /briefing/i);
});

test('storage falls back to memory when localStorage throws', () => {
  const key = 'briefing.test.denied';
  const original = global.localStorage;
  global.localStorage = {
    setItem() { throw new Error('denied'); },
    getItem() { throw new Error('denied'); },
    removeItem() { throw new Error('denied'); },
  };
  try {
    BriefingStorage.writeJSON(key, { seen: ['a'] });
    assert.deepEqual(BriefingStorage.readJSON(key, {}), { seen: ['a'] });
    assert.equal(BriefingStorage.isDenied(), true);
  } finally {
    global.localStorage = original;
  }
});

test('fetch poller completes on exact request acknowledgment', async () => {
  let polls = 0;
  const poller = new BriefingRefresh.FetchPoller({
    maxMs: 5000,
    fetchFn: async () => ({ ok: true, json: async () => lifecycle.transitions[2] }),
    loadBriefing: async () => {
      polls += 1;
      return polls >= 2 ? noChange : { ...noChange, request_ids: ['req_20260923T110000Z_sched'] };
    },
  });
  const done = new Promise(resolve => {
    poller.onComplete = resolve;
    poller.onError = err => resolve({ error: err });
  });
  await poller.start({
    requestId: manualRequest,
    statusUrl: 'https://worker.example/refresh/job_01HABC',
    baselineEdition: { generated_at: noChange.generated_at, request_ids: ['req_20260923T110000Z_sched'] },
  });
  const result = await done;
  assert.equal(result.status, 'no_change');
  assert.equal(poller.running, false);
});

test('fetch poller stops on unrelated newer edition', async () => {
  const poller = new BriefingRefresh.FetchPoller({
    maxMs: 3000,
    fetchFn: async () => ({ ok: false }),
    loadBriefing: async () => ({
      generated_at: '2026-09-23T18:00:00.000Z',
      request_ids: ['req_other'],
    }),
  });
  const err = await new Promise(resolve => {
    poller.onError = resolve;
    poller.start({
      requestId: manualRequest,
      baselineEdition: { generated_at: noChange.generated_at, request_ids: ['req_20260923T110000Z_sched'] },
    });
  });
  assert.equal(err.code, 'unrelated');
});

test('fetch poller handles rate limit from status endpoint', async () => {
  const poller = new BriefingRefresh.FetchPoller({
    maxMs: 3000,
    fetchFn: async () => ({
      ok: true,
      json: async () => lifecycle.limit_example,
    }),
    loadBriefing: async () => noChange,
  });
  const err = await new Promise(resolve => {
    poller.onError = resolve;
    poller.start({ requestId: manualRequest, statusUrl: 'https://worker.example/refresh/x' });
  });
  assert.equal(err.code, 'rate_limited');
});

test('fetch poller times out without acknowledgment', async () => {
  const poller = new BriefingRefresh.FetchPoller({
    maxMs: 50,
    pollDelays: [30],
    fetchFn: async () => ({ ok: false }),
    loadBriefing: async () => ({ generated_at: noChange.generated_at, request_ids: ['req_old'] }),
  });
  const err = await new Promise(resolve => {
    poller.onError = resolve;
    poller.start({ requestId: manualRequest, baselineEdition: { generated_at: noChange.generated_at } });
  });
  assert.equal(err.code, 'timeout');
});

test('startFetchPoll replaces an existing poller', async () => {
  const first = BriefingRefresh.startFetchPoll({
    maxMs: 10000,
    fetchFn: async () => ({ ok: false }),
    loadBriefing: async () => null,
    onStatus: () => {},
    onComplete: () => {},
    onError: () => {},
  });
  await first.start({ requestId: 'req_a' });
  assert.equal(first.running, true);
  const second = BriefingRefresh.startFetchPoll({
    maxMs: 10000,
    fetchFn: async () => ({ ok: false }),
    loadBriefing: async () => null,
    onStatus: () => {},
    onComplete: () => {},
    onError: () => {},
  });
  await second.start({ requestId: 'req_b' });
  assert.equal(first.running, false);
  assert.equal(second.running, true);
  second.stop();
});

test('two-click guard uses pending fetch record', () => {
  const key = 'briefing.pendingFetch.v1';
  BriefingStorage.removeItem(key);
  BriefingStorage.writeJSON(key, { requestId: manualRequest, startedAt: Date.now() });
  const pending = BriefingStorage.readJSON(key, null);
  assert.equal(pending.requestId, manualRequest);
  BriefingStorage.removeItem(key);
});

test('archived legacy edition remains readable in helpers', () => {
  assert.equal(BriefingRefresh.sectionAgeNote(archived.sections.news, archived), '');
  assert.equal(BriefingRefresh.requestAcknowledged(archived, 'anything'), false);
});
