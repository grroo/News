/* Cross-language release path. Real JWT verification/guard/builder/parser;
 * generated test keys, feed fixtures, provider HTTP and publication are local. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const Refresh = require('../site/refresh.js');
const ROOT = path.resolve(__dirname, '..');
const PYTHON = process.env.NEWS_TEST_PYTHON || 'python3';
const schedule = require('../schedule.json');
const load = name => require('./fixtures/contracts/' + name);

async function harness(nowText = '2026-09-04T17:05:00Z') {
  const auth = await import('../cloudflare/news-scheduler/auth.mjs');
  const http = await import('../cloudflare/news-scheduler/http.mjs');
  const service = await import('../cloudflare/news-scheduler/reservations.mjs');
  const key = await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5', modulusLength:2048, publicExponent:new Uint8Array([1,0,1]), hash:'SHA-256'}, true, ['sign','verify']);
  const jwk = await crypto.subtle.exportKey('jwk', key.publicKey); jwk.kid = 'test';
  const now = Date.parse(nowText);
  const env = {REFRESH_ENABLED:'true', GITHUB_TOKEN:'test-only', NEWS_RESERVATION_GATE_TOKEN:'test-claim', GITHUB_REPO:'grroo/News', GITHUB_WORKFLOW:'build.yml', ACCESS_TEAM_DOMAIN:'release-test', ACCESS_AUD:'release-aud', OWNER_EMAIL:'owner@example.test', PAGES_SITE_URL:'https://pages.example.test/News/'};
  const token = await auth.signJwt({iss:'https://release-test.cloudflareaccess.com',aud:env.ACCESS_AUD,email:env.OWNER_EMAIL,exp:now/1000+3600},key.privateKey);
  let state = service.emptyControl();
  const dispatches = [];
  const deps = {now, schedule, keys:[jwk], publicStatus:async()=>service.publicProjection(state),
    refresh:async ({idempotencyKey})=>{const r=service.requestRefresh(state,{now,schedule,idempotencyKey}); state=r.state; return r;},
    claim:async claim=>{const r=service.applyClaim(state,claim,nowText);state=r.state;return r;},
    readJob:async id=>state.jobs[id], dispatchWorkflow:async body=>{dispatches.push(body);state=service.noteDispatchResult(state,'accepted');}};
  return {env, deps, now, service, dispatches, token,
    get state(){return state;}, set state(next){state=JSON.parse(JSON.stringify(next));},
    handle:req=>http.handleRequest(req,env,deps),
    post:(key, overrides={})=>http.handleRequest(new Request('https://worker.example.test/refresh',{method:'POST',headers:{'cf-access-jwt-assertion':token,origin:'https://worker.example.test','content-type':'application/json','x-requested-with':'news-refresh',...overrides},body:JSON.stringify({idempotency_key:key})}),env,deps),
  };
}
function fixtures(root) {
  const proc=spawnSync(PYTHON,[path.join(ROOT,'tests/make_fixtures.py'),'--output-dir',path.join(root,'fixtures')],{encoding:'utf8'});
  assert.equal(proc.status,0,proc.stderr);
}
function build(root, inputs, now, extra={}) {
  const proc=spawnSync(PYTHON,[path.join(ROOT,'tests/release/build_driver.py')],{input:JSON.stringify({root,inputs,now,...extra}),encoding:'utf8'});
  assert.equal(proc.status,0,proc.stderr);
  return JSON.parse(proc.stdout);
}

test('signed owner POST -> coalesced schedule -> single claim/build -> exact Pages acknowledgment',async()=>{
  const h=await harness();
  const root=fs.mkdtempSync(path.join(ROOT,'.release-'));
  try {
    fixtures(root);
    const accepted=await h.post('click-1'); assert.equal(accepted.status,202);
    const first=await accepted.json();
    const joined=await (await h.post('click-2')).json();
    assert.equal(joined.request_id,first.request_id);
    assert.equal((await (await h.post('click-1')).json()).job_id,first.job_id);
    assert.equal(h.dispatches.length,1);
    const prior={generated_at:'2026-09-04T11:05:00Z',mode:'llm'};
    const overlap=h.service.advanceScheduled(h.state,{now:h.now,schedule,runs:[],runsListed:true,live:prior,strict:true});
    assert.equal(overlap.dispatch,null); h.state=overlap.state;
    const inputs=h.dispatches[0].inputs;
    const claim={reservation_id:inputs.reservation_id,request_id:inputs.request_id,run_id:42,run_attempt:1,repository:'grroo/News',workflow:'build.yml',ref:'refs/heads/main'};
    const claimRequest=body=>new Request('https://worker.example.test/internal/reservations/claim',{method:'POST',headers:{authorization:'Bearer test-claim','content-type':'application/json'},body:JSON.stringify(body)});
    assert.equal((await (await h.handle(claimRequest(claim))).json()).decision,'allowed');
    assert.equal((await (await h.handle(claimRequest({...claim,run_attempt:2}))).json()).decision,'denied');
    const result=build(root,inputs,'2026-09-04T17:06:00Z');
    assert.equal(result.calls.length,3);
    assert.ok(result.edition.request_ids.includes(first.request_id));
    assert.equal(result.edition.quality.overall,'healthy');
    const unrelated={...result.edition,request_ids:['req-other']};
    h.state=h.service.reconcile(h.state,{now:h.now+120000,schedule,runs:[],runsListed:true,live:unrelated}).state;
    assert.equal(h.state.jobs[first.job_id].status,'publishing');
    h.state=h.service.reconcile(h.state,{now:h.now+120000,schedule,runs:[],runsListed:true,live:result.edition}).state;
    for(const job of [first,joined]) assert.equal(h.state.jobs[job.job_id].status,'succeeded');
    assert.equal(Refresh.requestAcknowledged(result.edition,joined.request_id),true);
    assert.equal(h.dispatches.length,1);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('scheduler first, then manual, finishes every coalesced job on the scheduled fast path',async()=>{
  const h=await harness('2026-09-23T11:05:00Z');
  const due=h.service.advanceScheduled(h.state,{now:h.now,schedule,runs:[],runsListed:true,live:{generated_at:'2026-09-23T05:00:00Z',mode:'llm'},strict:true});
  h.state=due.state; assert.ok(due.dispatch);
  const manual=await (await h.post('join-scheduled')).json();
  assert.equal(h.dispatches.length,0);
  const edition=structuredClone(load('healthy-edition.json'));
  edition.request_ids=[manual.request_id];
  h.state=h.service.advanceScheduled(h.state,{now:h.now,schedule,runs:[],runsListed:true,live:edition,strict:true}).state;
  assert.equal(h.state.phase,'published');
  assert.ok(Object.values(h.state.jobs).every(j=>j.status==='succeeded'));
});

test('explicit manual request after an already-satisfied slot still builds and can publish no-change',async()=>{
  const h=await harness('2026-09-23T11:10:00Z');
  const edition=structuredClone(load('healthy-edition.json'));
  h.state=h.service.advanceScheduled(h.state,{now:h.now,schedule,runs:[],runsListed:true,live:edition,strict:true}).state;
  assert.equal(h.state.phase,'published');
  assert.equal((await h.post('explicit-refresh')).status,202);
  assert.equal(h.dispatches.length,1);
  assert.equal(h.dispatches[0].inputs.scheduled_slot,undefined);
  const root=fs.mkdtempSync(path.join(ROOT,'.release-'));
  try {
    fixtures(root);
    build(root,{request_id:'earlier',reservation_id:'res-earlier'},'2026-09-04T17:00:00Z');
    const result=build(root,h.dispatches[0].inputs,'2026-09-04T18:00:00Z',{live:edition});
    assert.equal(result.allowed,true); assert.equal(result.calls.length,0);
    assert.equal(result.edition.refresh.outcome,'no_change');
    assert.ok(result.edition.request_ids.includes(h.dispatches[0].inputs.request_id));
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('v2 failed/degraded and no-change decisions apply before strict migration; health wins over outcome',async()=>{
  const h=await harness('2026-09-23T17:10:00Z');
  for(const name of ['degraded-edition.json','provider-failure-edition.json']) {
    const live=structuredClone(load(name));
    const result=h.service.advanceScheduled(h.service.emptyControl(),{now:h.now,schedule,runs:[],runsListed:true,live,strict:false});
    assert.notEqual(result.state.phase,'published');
    live.refresh.outcome='no_change';
    assert.equal(Refresh.publicationOutcome(live),live.quality.overall);
  }
  const live=structuredClone(load('no-change-result.json'));
  const result=h.service.advanceScheduled(h.service.emptyControl(),{now:h.now,schedule,runs:[],runsListed:true,live,strict:false});
  assert.equal(result.state.phase,'published'); assert.equal(result.dispatch,null);
});

test('unauthorized and exhausted budget never dispatch; expired coalesced jobs never cite unrelated publication',async()=>{
  const h=await harness();
  for(const headers of [{'cf-access-jwt-assertion':''},{'cf-access-jwt-assertion':h.token+'bad'},{origin:'https://evil.example'}]) {
    assert.ok((await h.post('denied',headers)).status>=400);
  }
  assert.equal(h.dispatches.length,0);
  h.state={...h.state,daily:{rome_day:'2026-09-04',manual_count:5,generation_count:8}};
  assert.equal((await h.post('exhausted')).status,429);assert.equal(h.dispatches.length,0);
  h.state=h.service.emptyControl();
  const a=await (await h.post('a')).json(), b=await (await h.post('b')).json();
  const later=h.service.reconcile(h.state,{now:h.now+21*60000,schedule,runs:[],runsListed:true,live:{edition_id:'unrelated',request_ids:['other']}});
  for(const job of [a,b]) {
    assert.equal(later.state.jobs[job.job_id].status,'expired');
    assert.equal(later.state.jobs[job.job_id].published_edition_id,undefined);
  }
});

test('all three Rome slots preserve local hours through both DST transitions',async()=>{
  const {slotAtHour}=await import('../cloudflare/news-scheduler/core.mjs');
  for(const [day,hours] of [['2026-03-28',[6,12,18]],['2026-03-29',[5,11,17]],['2026-10-24',[5,11,17]],['2026-10-25',[6,12,18]]]) {
    assert.deepEqual(schedule.hours.map(hour=>new Date(slotAtHour(Date.parse(day+'T12:00:00Z'),schedule,hour)).getUTCHours()),hours,day);
  }
});
