import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {signJwt, timingSafeEqual, verifyAccessJwt} from './auth.mjs';
import {handleRequest, safeReturnUrl} from './http.mjs';
import {
  advanceScheduled, applyClaim, claimDecision, emptyControl, noteDispatchResult, publicationFor,
  reconcile, requestRefresh, satisfiesHealthySlot,
} from './reservations.mjs';
import {romeDay} from './core.mjs';

const schedule = JSON.parse(readFileSync(new URL('../../schedule.json', import.meta.url)));
const fixtures = new URL('../../tests/fixtures/contracts/', import.meta.url);
const load = name => JSON.parse(readFileSync(new URL(name, fixtures)));
const now = Date.parse('2026-09-23T15:30:00Z');
const slot = '2026-09-23T11:00:00.000Z';

function materialize(c) {
  const value = structuredClone(c.fixture ? load(c.fixture) : c.instance);
  const target = pointer => {
    const parts = pointer.slice(1).split('/');
    let parent = value;
    for (const part of parts.slice(0, -1)) parent = parent[part];
    return [parent, parts.at(-1)];
  };
  for (const [pointer, replacement] of Object.entries(c.set || {})) {
    const [parent, key] = target(pointer);
    parent[key] = replacement;
  }
  for (const pointer of c.delete || []) {
    const [parent, key] = target(pointer);
    delete parent[key];
  }
  return value;
}

function harness(state = emptyControl(slot)) {
  const dispatches = [];
  const env = {
    REFRESH_ENABLED: 'true', GITHUB_TOKEN: 'server-only', GITHUB_REPO: 'grroo/News', GITHUB_WORKFLOW: 'build.yml',
    ACCESS_TEAM_DOMAIN: 'team', ACCESS_AUD: 'aud', OWNER_EMAIL: 'owner@example.com', PAGES_SITE_URL: 'https://example.test/News/',
    NEWS_RESERVATION_GATE_TOKEN: 'gate-secret',
  };
  let control = state;
  const deps = {
    now, schedule,
    verify: async () => ({ok: true, email: 'owner@example.com'}),
    publicStatus: async () => ({phase: control.phase}),
    refresh: async ({idempotencyKey}) => {
      const result = requestRefresh(control, {now, schedule, idempotencyKey, trigger: 'manual'});
      control = result.state;
      return result;
    },
    dispatchWorkflow: async body => {
      dispatches.push(body);
      control = noteDispatchResult(control, 'accepted');
    },
    claim: async claim => {
      const result = applyClaim(control, claim, '2026-09-23T15:31:00.000Z');
      control = result.state;
      return result;
    },
    readJob: async id => control.jobs[id] || null,
  };
  return {env, deps, dispatches, read: () => control, replace: next => { control = next; }};
}

function post(key, extra = {}) {
  return new Request('https://news.example/refresh', {
    method: 'POST',
    headers: {'content-type': 'application/json', origin: 'https://news.example', 'x-requested-with': 'news-refresh', 'cf-access-jwt-assertion': 'token', ...(extra.headers || {})},
    body: JSON.stringify({idempotency_key: key}),
  });
}

test('shared slot and claim cases match the production decisions', () => {
  for (const c of load('slot-cases.json').cases) {
    assert.equal(satisfiesHealthySlot(materialize(c), c.slot, Date.parse(c.now), c.request_id), c.expected, c.name);
  }
  for (const c of load('reservation-cases.json').cases) {
    const decision = claimDecision(c.reservation, c.claim, c.now);
    assert.equal(decision.decision, c.expected_decision, c.name);
    assert.equal(decision.reason, c.expected_reason, c.name);
  }
});

test('Rome day rolls at local midnight and stays stable across both DST transitions', () => {
  assert.equal(romeDay(Date.parse('2026-09-22T21:59:00Z')), '2026-09-22');
  assert.equal(romeDay(Date.parse('2026-09-22T22:00:00Z')), '2026-09-23');
  assert.equal(romeDay(Date.parse('2026-03-28T23:30:00Z')), '2026-03-29');
  assert.equal(romeDay(Date.parse('2026-03-29T01:30:00Z')), '2026-03-29');
  assert.equal(romeDay(Date.parse('2026-10-24T21:30:00Z')), '2026-10-24');
  assert.equal(romeDay(Date.parse('2026-10-24T22:30:00Z')), '2026-10-25');
  assert.equal(romeDay(Date.parse('2026-10-25T01:30:00Z')), '2026-10-25');
});

test('repeated and concurrent refreshes dispatch once and limits survive a restart', async () => {
  const h = harness();
  const first = await handleRequest(post('click-1'), h.env, h.deps);
  const created = await first.json();
  assert.equal(first.status, 202);
  assert.equal(created.status, 'accepted');
  assert.equal(h.dispatches.length, 1);
  assert.equal(h.dispatches[0].inputs.deploy_only, false);
  assert.equal(h.dispatches[0].inputs.mock, false);
  const again = await (await handleRequest(post('click-1'), h.env, h.deps)).json();
  assert.equal(again.job_id, created.job_id);
  assert.equal(h.dispatches.length, 1);
  const second = await (await handleRequest(post('click-2'), h.env, h.deps)).json();
  assert.notEqual(second.job_id, created.job_id);
  assert.equal(second.request_id, created.request_id);
  assert.equal(h.dispatches.length, 1);
  assert.deepEqual(h.read().reservation.request_ids, [created.request_id]);
  const restarted = JSON.parse(JSON.stringify(h.read()));
  assert.equal(restarted.daily.manual_count, 1);
  assert.equal(restarted.daily.generation_count, 1);
  restarted.jobs[created.job_id].status = 'succeeded';
  restarted.active_job_id = null;
  restarted.reservation = null;
  h.replace(restarted);
  const limited = await (await handleRequest(post('click-3'), h.env, h.deps)).json();
  assert.equal(limited.error, 'rate_limited');
  assert.ok(limited.retry_after);
});

test('unauthorized, forged, expired, and cross-site requests do not dispatch', async () => {
  const pair = await crypto.subtle.generateKey({name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256'}, true, ['sign', 'verify']);
  const jwk = await crypto.subtle.exportKey('jwk', pair.publicKey);
  jwk.kid = 'test';
  jwk.alg = 'RS256';
  const base = {iss: 'https://team.cloudflareaccess.com', aud: 'aud', email: 'owner@example.com'};
  const good = await signJwt({...base, exp: Math.floor(Date.now() / 1000) + 60}, pair.privateKey);
  assert.equal((await verifyAccessJwt(good, {teamDomain: 'team', audience: 'aud', ownerEmail: 'owner@example.com', keys: [jwk]})).ok, true);
  const expired = await signJwt({...base, exp: Math.floor(Date.now() / 1000) - 10}, pair.privateKey);
  assert.equal((await verifyAccessJwt(expired, {teamDomain: 'team', audience: 'aud', ownerEmail: 'owner@example.com', keys: [jwk]})).reason, 'expired');
  const other = await signJwt({...base, email: 'other@example.com', exp: Math.floor(Date.now() / 1000) + 60}, pair.privateKey);
  assert.equal((await verifyAccessJwt(other, {teamDomain: 'team', audience: 'aud', ownerEmail: 'owner@example.com', keys: [jwk]})).reason, 'owner');
  assert.equal((await verifyAccessJwt(good.slice(0, -2) + (good.endsWith('a') ? 'b' : 'a'), {teamDomain: 'team', audience: 'aud', ownerEmail: 'owner@example.com', keys: [jwk]})).reason, 'forged');
  assert.equal((await verifyAccessJwt(await signJwt({...base, email: null, exp: Math.floor(Date.now() / 1000) + 60, common_name: 'svc'}, pair.privateKey), {teamDomain: 'team', audience: 'aud', ownerEmail: 'owner@example.com', keys: [jwk]})).reason, 'service_token');

  const h = harness();
  h.deps.verify = async token => verifyAccessJwt(token, {teamDomain: 'team', audience: 'aud', ownerEmail: 'owner@example.com', keys: [jwk]});
  for (const request of [
    post('x', {headers: {'cf-access-jwt-assertion': ''}}),
    post('x', {headers: {'cf-access-jwt-assertion': expired}}),
    new Request('https://news.example/refresh', {method: 'POST', headers: {'content-type': 'application/json', origin: 'https://evil.test', 'x-requested-with': 'news-refresh', 'cf-access-jwt-assertion': good}, body: '{}'}),
    new Request('https://news.example/refresh', {method: 'GET', headers: {'cf-access-jwt-assertion': good}}),
    new Request('https://news.example/health'),
  ]) {
    await handleRequest(request, h.env, h.deps);
  }
  assert.equal(h.dispatches.length, 0);
  const page = await handleRequest(new Request('https://news.example/', {headers: {'cf-access-jwt-assertion': good}}), h.env, h.deps);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /Fetch new briefing/);
  assert.equal(h.dispatches.length, 0);

  const offline = harness();
  delete offline.deps.verify;
  offline.env.ACCESS_TEAM_DOMAIN = 'team-down';
  offline.deps.fetchCerts = async () => new Response('unavailable', {status: 503});
  const unavailable = await handleRequest(post('offline', {headers: {'cf-access-jwt-assertion': good}}), offline.env, offline.deps);
  assert.equal(unavailable.status, 503);
  assert.equal((await unavailable.json()).error, 'identity_unavailable');
  assert.equal(offline.dispatches.length, 0);

  const online = harness();
  delete online.deps.verify;
  online.env.ACCESS_TEAM_DOMAIN = 'team';
  online.deps.fetchCerts = async () => Response.json({keys: [jwk]});
  const accepted = await handleRequest(post('from-certs', {headers: {'cf-access-jwt-assertion': good}}), online.env, online.deps);
  assert.equal(accepted.status, 202);
  assert.equal(online.dispatches.length, 1);
});

test('claim requires the gate token and does not dispatch', async () => {
  const h = harness();
  await handleRequest(post('click-1'), h.env, h.deps);
  const reservation = h.read().reservation;
  const claim = {reservation_id: reservation.reservation_id, request_id: reservation.request_id, run_id: 50, run_attempt: 1, repository: 'grroo/News', workflow: 'build.yml', ref: 'refs/heads/main'};
  const denied = await handleRequest(new Request('https://news.example/internal/reservations/claim', {method: 'POST', headers: {authorization: 'Bearer wrong', 'content-type': 'application/json'}, body: JSON.stringify(claim)}), h.env, h.deps);
  assert.equal(denied.status, 401);
  assert.equal(h.read().reservation.status, 'reserved');
  const allowed = await handleRequest(new Request('https://news.example/internal/reservations/claim', {method: 'POST', headers: {authorization: 'Bearer gate-secret', 'content-type': 'application/json'}, body: JSON.stringify(claim)}), h.env, h.deps);
  assert.equal((await allowed.json()).decision, 'allowed');
  assert.equal(h.read().reservation.status, 'claimed');
  assert.equal(h.dispatches.length, 1);
  assert.equal(timingSafeEqual('gate-secret', 'gate-secret'), true);
  assert.equal(timingSafeEqual('gate-secret', 'gate-secretx'), false);
});

test('ambiguous dispatch is reconciled, stale editions do not complete another request, and deploy-only makes no model call', () => {
  let state = emptyControl();
  const created = requestRefresh(state, {now, schedule, idempotencyKey: 'click', trigger: 'manual', ids: {job_id: 'job_1', request_id: 'req_20260923T110000Z_sched', reservation_id: 'res_1'}});
  state = noteDispatchResult(created.state, 'timeout');
  assert.equal(state.dispatch_ambiguous, true);
  assert.equal(state.reservation.status, 'reserved');
  const waiting = reconcile(state, {now: now + 1000, schedule, runs: [], runsListed: true, live: {generated_at: slot, schema_version: 2, request_ids: ['someone-else'], refresh: {outcome: 'generated'}, quality: {overall: 'healthy'}, edition_id: 'other'}});
  assert.equal(waiting.dispatch, null);
  assert.equal(waiting.state.jobs.job_1.status, 'queued');
  assert.equal(publicationFor({schema_version: 2, request_ids: ['other'], edition_id: 'other', quality: {overall: 'healthy'}, refresh: {outcome: 'generated'}}, 'req_20260923T110000Z_sched'), null);
  const found = reconcile(state, {now: now + 60000, schedule, runsListed: true, runs: [{id: 77, status: 'completed', conclusion: 'success', created_at: new Date(now).toISOString(), event: 'workflow_dispatch', name: 'build req_20260923T110000Z_sched'}], live: {generated_at: slot, schema_version: 2, request_ids: ['req_other'], edition_id: 'edition-other', quality: {overall: 'healthy'}, refresh: {outcome: 'generated'}}});
  assert.equal(found.dispatch.inputs.deploy_only, true);
  assert.equal(found.state.daily.generation_count, 1);
  const published = reconcile(found.state, {now: now + 120000, schedule, runsListed: true, runs: [], live: load('healthy-edition.json')});
  assert.equal(published.state.jobs.job_1.status, 'succeeded');
  assert.equal(published.state.jobs.job_1.published_edition_id, 'req_20260923T110000Z_sched');
});

test('allowance exhaustion and a failed workflow do not look published', () => {
  let state = emptyControl();
  state.daily = {rome_day: '2026-09-23', manual_count: 5, generation_count: 5};
  const blocked = requestRefresh(state, {now, schedule, idempotencyKey: 'more', trigger: 'manual'});
  assert.equal(blocked.body.error, 'daily_limit');
  const scheduled = advanceScheduled(emptyControl(), {
    now, schedule, runs: [], runsListed: true, tokenPresent: true, strict: true,
    live: {...load('provider-failure-edition.json'), generated_at: new Date(now).toISOString(), mode: 'llm'},
  });
  assert.notEqual(scheduled.state.phase, 'published');
  assert.equal(scheduled.dispatch.inputs.reservation_id.length > 0, true);
  const legacy = advanceScheduled(emptyControl(), {now: Date.parse('2026-09-07T05:05:00Z'), schedule, runs: [], runsListed: true, tokenPresent: true, strict: false, live: {generated_at: '2026-09-07T05:00:00.000Z', mode: 'llm'}});
  assert.equal(legacy.state.phase, 'published');
  assert.equal(legacy.dispatch, null);
});

test('scheduler collision coalesces into the scheduled reservation', () => {
  const started = advanceScheduled(emptyControl(), {
    now, schedule, runs: [], runsListed: true, tokenPresent: true, strict: true,
    live: {generated_at: '2026-09-23T09:00:00Z', mode: 'llm', schema_version: 1},
  });
  const manual = requestRefresh(started.state, {now: now + 1000, schedule, idempotencyKey: 'owner', trigger: 'manual'});
  assert.equal(manual.dispatch, null);
  assert.equal(manual.coalesced, true);
  assert.equal(manual.state.daily.generation_count, 1);
  assert.equal(manual.state.jobs[manual.body.job_id].request_id, started.state.reservation.request_id);
  assert.deepEqual(manual.state.reservation.request_ids, [started.state.reservation.request_id]);
});

test('a coalesced job stays open when Pages lists only the other request id', () => {
  const first = requestRefresh(emptyControl(), {
    now, schedule, idempotencyKey: 'a', trigger: 'manual',
    ids: {job_id: 'job_first', request_id: 'req_first', reservation_id: 'res_1'},
  });
  const second = requestRefresh(first.state, {
    now: now + 1000, schedule, idempotencyKey: 'b', trigger: 'manual',
    ids: {job_id: 'job_second', request_id: 'req_second'},
  });
  assert.equal(second.coalesced, true);
  assert.equal(second.state.jobs.job_second.request_id, 'req_first');
  const divergent = second.state;
  divergent.jobs.job_second.request_id = 'req_second';
  divergent.reservation.request_ids = ['req_first', 'req_second'];
  const live = {
    schema_version: 2, request_ids: ['req_first'], edition_id: 'ed-first', generated_at: new Date(now).toISOString(),
    quality: {overall: 'healthy'}, refresh: {outcome: 'generated'},
  };
  const published = reconcile(divergent, {now: now + 5000, schedule, runs: [], runsListed: true, live});
  assert.equal(published.state.jobs.job_first.status, 'succeeded');
  assert.equal(published.state.jobs.job_second.status, 'queued');
  assert.equal(publicationFor(live, 'req_second'), null);
});

test('an unrelated or not-yet-listed run does not bind, refund, or dispatch again', () => {
  const created = requestRefresh(emptyControl(), {
    now, schedule, idempotencyKey: 'click', trigger: 'manual',
    ids: {job_id: 'job_1', request_id: 'req_first', reservation_id: 'res_1'},
  });
  const state = noteDispatchResult(created.state, 'timeout');
  const unrelated = {
    id: 99, status: 'completed', conclusion: 'success', event: 'workflow_dispatch',
    created_at: new Date(now).toISOString(), name: 'Build briefing & deploy', display_title: 'manual',
  };
  const live = {generated_at: slot, schema_version: 2, request_ids: ['req_other'], edition_id: 'other', quality: {overall: 'healthy'}, refresh: {outcome: 'generated'}};
  const alone = reconcile(state, {now: now + 16 * 60 * 1000, schedule, runsListed: true, runs: [unrelated], live});
  assert.equal(alone.dispatch, null);
  assert.equal(alone.state.runId, null);
  assert.equal(alone.state.jobs.job_1.status, 'queued');
  assert.equal(alone.state.reservation.status, 'reserved');
  assert.equal(alone.state.daily.generation_count, 1);
  const delayed = reconcile(state, {now: now + 16 * 60 * 1000, schedule, runsListed: false, runs: [], live});
  assert.equal(delayed.dispatch, null);
  assert.equal(delayed.state.reservation.status, 'reserved');
  assert.equal(delayed.state.daily.generation_count, 1);
  const expired = reconcile(state, {now: now + 21 * 60 * 1000, schedule, runsListed: true, runs: [unrelated], live});
  assert.equal(expired.dispatch, null);
  assert.equal(expired.state.jobs.job_1.status, 'expired');
  assert.equal(expired.state.daily.generation_count, 1);
  const again = advanceScheduled(expired.state, {now: now + 22 * 60 * 1000, schedule, runsListed: true, runs: [unrelated], live, tokenPresent: true, strict: false});
  assert.equal(again.dispatch, null);
  assert.equal(again.state.phase, 'attention_needed');
  const claimed = applyClaim(state, {
    reservation_id: 'res_1', request_id: 'req_first', run_id: 77, run_attempt: 1,
    repository: 'grroo/News', workflow: 'build.yml', ref: 'refs/heads/main',
  }, new Date(now + 1000).toISOString());
  const bound = reconcile(claimed.state, {
    now: now + 120000, schedule, runsListed: true, live,
    runs: [unrelated, {id: 77, status: 'completed', conclusion: 'success', event: 'workflow_dispatch', created_at: new Date(now + 30000).toISOString(), name: 'Build briefing & deploy'}],
  });
  assert.equal(bound.state.runId, 77);
  assert.equal(bound.dispatch.inputs.deploy_only, true);
  assert.equal(bound.dispatch.inputs.request_id, 'req_first');
});

test('owner return URL stays on the configured Pages origin', async () => {
  assert.equal(safeReturnUrl('https://evil.test/steal', 'https://example.test/News/'), '');
  assert.equal(safeReturnUrl('javascript:alert(1)', 'https://example.test/News/'), '');
  assert.equal(safeReturnUrl('https://example.test/News/#/', 'https://example.test/News/'), 'https://example.test/News/#/');
  const h = harness();
  const rejected = await handleRequest(new Request('https://news.example/?return=' + encodeURIComponent('https://evil.test/steal'), {headers: {'cf-access-jwt-assertion': 'token'}}), h.env, h.deps);
  const rejectedHtml = await rejected.text();
  assert.equal(rejected.status, 200);
  assert.match(rejectedHtml, /https:\/\/example\.test\/News\//);
  assert.doesNotMatch(rejectedHtml, /evil\.test/);
  const accepted = await handleRequest(new Request('https://news.example/?return=' + encodeURIComponent('https://example.test/News/#/'), {headers: {'cf-access-jwt-assertion': 'token'}}), h.env, h.deps);
  const html = await accepted.text();
  assert.match(html, /https:\/\/example\.test\/News\//);
  assert.match(html, /searchParams\.set\('request'/);
  assert.doesNotMatch(html, /searchParams\.set\('status'/);
  assert.equal(h.dispatches.length, 0);
});
