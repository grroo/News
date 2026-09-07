// No platform dependencies: the scheduler's decisions are regression-tested in Node.
export function dueSlot(now, schedule) {
  const fields = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone: schedule.timezone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(new Date(now)).filter(p => p.type !== 'literal').map(p => [p.type, Number(p.value)]));
  const localNow = Date.UTC(fields.year, fields.month-1, fields.day, fields.hour, fields.minute);
  let localDue;
  for (const hour of schedule.hours) {
    const candidate = Date.UTC(fields.year, fields.month-1, fields.day, hour, schedule.minute);
    if (candidate <= localNow) localDue = candidate;
  }
  if (localDue === undefined) localDue = Date.UTC(fields.year, fields.month-1, fields.day-1, schedule.hours.at(-1), schedule.minute);
  // Resolve offset at the slot itself, including the night of a DST transition.
  let utc = localDue;
  for (let i=0; i<3; i++) {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {timeZone:schedule.timezone,
      year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'
    }).formatToParts(new Date(utc)).filter(p=>p.type!=='literal').map(p=>[p.type,Number(p.value)]));
    utc += localDue - Date.UTC(p.year,p.month-1,p.day,p.hour,p.minute);
  }
  return new Date(utc).toISOString();
}

async function json(response, limit = 1024 * 1024) {
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  // Bound external responses before parsing; never log headers or bodies.
  const reader = response.body.getReader();
  const chunks = []; let size = 0;
  while (true) {
    const {done,value} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) { await reader.cancel(); throw new Error('Response too large'); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk,offset); offset+=chunk.byteLength; }
  return JSON.parse(new TextDecoder().decode(bytes));
}

export async function tick({now, schedule, state, env, request = fetch, save}) {
  const slot = dueSlot(now, schedule);
  state = state.slot === slot ? {...state} : {slot, attempts:0, phase:'due', nextAttempt:0};
  state.checkedAt = new Date(now).toISOString();
  // A successful dispatch is not publication. Always inspect the live Pages data.
  const live = await json(await request(`${env.BRIEFING_URL}?scheduler=${now}`, {
    cache:'no-store', redirect:'error', signal:AbortSignal.timeout(10000)
  }));
  const generated = Date.parse(live.generated_at);
  if (!Number.isFinite(generated)) throw new Error('Live briefing has no valid timestamp');
  state.liveGeneratedAt = live.generated_at;
  if (generated >= Date.parse(slot) && generated <= now + 60000 && live.mode === 'llm') {
    return {...state, phase:'published', error:null};
  }
  if (!env.GITHUB_TOKEN) return {...state, phase:'needs_github_secret'};
  if (state.nextAttempt > now) return {...state, phase:'waiting'};
  const api = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}`;
  const headers = {Authorization:`Bearer ${env.GITHUB_TOKEN}`, Accept:'application/vnd.github+json',
    'X-GitHub-Api-Version':'2022-11-28', 'User-Agent':'news-briefing-scheduler'};
  const runs = await json(await request(`${api}/runs?branch=main&per_page=20`, {
    headers, redirect:'error', signal:AbortSignal.timeout(10000)
  }));
  if (!Array.isArray(runs.workflow_runs)) throw new Error('GitHub returned no run list');
  const active = runs.workflow_runs.find(r => r.status !== 'completed');
  if (active) return {...state, phase:'build_in_progress', runId:active.id,
    error:now-Date.parse(active.created_at)>45*60000 ? 'Build has been pending for over 45 minutes' : null};
  if (state.attempts >= 3) return {...state, phase:'attention_needed', error:'Three attempts without a published briefing'};
  // Persist BEFORE sending. An ambiguous timeout must not immediately dispatch again.
  state = {...state, attempts:state.attempts+1, phase:'dispatching', nextAttempt:now+15*60000, error:null};
  await save(state);
  const response = await request(`${api}/dispatches`, {
    method:'POST', headers:{...headers,'Content-Type':'application/json'},
    body:JSON.stringify({ref:'main',inputs:{mock:false,scheduled_slot:slot}}),
    redirect:'error', signal:AbortSignal.timeout(10000)
  });
  if (!response.ok) throw new Error(`GitHub dispatch HTTP ${response.status}`);
  if (response.body) await response.body.cancel();
  return {...state,phase:'dispatched'};
}
