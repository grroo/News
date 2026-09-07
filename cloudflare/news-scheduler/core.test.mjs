import {test} from 'node:test';
import assert from 'node:assert/strict';
import {dueSlot,tick} from './core.mjs';
import {readFileSync} from 'node:fs';
const schedule = JSON.parse(readFileSync(new URL('../../schedule.json',import.meta.url)));
const now = Date.parse('2026-09-07T05:05:00Z');
const slot = '2026-09-07T05:00:00.000Z';
const env = {BRIEFING_URL:'https://example.test/briefing.json',GITHUB_REPO:'grroo/News',GITHUB_WORKFLOW:'build.yml',GITHUB_TOKEN:'test-only'};
const stale = {generated_at:'2026-09-06T17:00:00Z',mode:'llm'};
function setup({live=stale,runs=[],state={},token='test-only',dispatch}={}) {
  const calls=[]; const saved=[];
  return {calls,saved,run:()=>tick({now,schedule,state,env:{...env,GITHUB_TOKEN:token},
    save:async s=>saved.push({...s}),request:async(url,options)=>{
      calls.push({url,options});
      if(url.startsWith(env.BRIEFING_URL))return Response.json(live);
      if(url.includes('/runs?'))return Response.json({workflow_runs:runs});
      assert.equal(saved.length,1,'persist cooldown before dispatch');
      return dispatch ? dispatch() : new Response(null,{status:204});
    }})};
}
test('Rome slots handle summer, winter, and both daylight saving transitions',()=>{
  for(const [time,expected] of [
    ['2026-09-07T05:00:00Z',slot],
    ['2026-09-07T04:59:00Z','2026-09-06T17:00:00.000Z'],
    ['2026-01-07T12:00:00Z','2026-01-07T12:00:00.000Z'],
    ['2026-03-29T04:59:00Z','2026-03-28T18:00:00.000Z'],
    ['2026-03-29T05:00:00Z','2026-03-29T05:00:00.000Z'],
    ['2026-10-25T05:59:00Z','2026-10-24T17:00:00.000Z'],
    ['2026-10-25T06:00:00Z','2026-10-25T06:00:00.000Z']
  ])assert.equal(dueSlot(Date.parse(time),schedule),expected);
});
test('live publication completes the slot without GitHub requests',async()=>{
  const t=setup({live:{generated_at:slot,mode:'llm'}});
  assert.equal((await t.run()).phase,'published');assert.equal(t.calls.length,1);
});
test('missing secret cannot dispatch',async()=>{
  const t=setup({token:''});assert.equal((await t.run()).phase,'needs_github_secret');assert.equal(t.calls.length,1);
});
test('active workflow prevents duplicates and reports excessive delay',async()=>{
  const t=setup({runs:[{status:'queued',id:42,created_at:'2026-09-07T04:00:00Z'}]});
  const s=await t.run();assert.equal(s.phase,'build_in_progress');assert.match(s.error,/45 minutes/);assert.equal(t.calls.length,2);
});
test('dispatch persists attempt first and identifies the intended slot',async()=>{
  const t=setup();const s=await t.run();assert.equal(s.phase,'dispatched');assert.equal(s.attempts,1);
  assert.deepEqual(JSON.parse(t.calls[2].options.body),{ref:'main',inputs:{mock:false,scheduled_slot:slot}});
});
test('ambiguous dispatch timeout keeps a cooldown',async()=>{
  const t=setup({dispatch:()=>{throw new Error('timeout')}});await assert.rejects(t.run,/timeout/);
  const retry=setup({state:t.saved[0]});assert.equal((await retry.run()).phase,'waiting');assert.equal(retry.calls.length,1);
});
test('stop after three attempts but reset for a new slot',async()=>{
  const t=setup({state:{slot,attempts:3}});assert.equal((await t.run()).phase,'attention_needed');assert.equal(t.calls.length,2);
  const next=setup({state:{slot:'old',attempts:3}});assert.equal((await next.run()).attempts,1);
});
test('successful GitHub run with stale live data still needs publication',async()=>{
  const t=setup({runs:[{status:'completed',conclusion:'success'}]});assert.equal((await t.run()).phase,'dispatched');
});
test('mock or future-dated content cannot count as success',async()=>{
  for(const live of [{generated_at:slot,mode:'mock'},{generated_at:'2099-01-01T00:00:00Z',mode:'llm'}]){
    assert.equal((await setup({live}).run()).phase,'dispatched');
  }
});
test('invalid live data fails without dispatching',async()=>{
  const t=setup({live:{}});await assert.rejects(t.run,/timestamp/);assert.equal(t.calls.length,1);
});
