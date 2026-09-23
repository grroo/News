// No platform dependencies: the scheduler's decisions are regression-tested in Node.
export function zonedParts(ms, timeZone) {
  return Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(new Date(ms)).filter(p => p.type !== 'literal').map(p => [p.type, Number(p.value)]));
}

export function resolveLocalUtc(localDue, timeZone) {
  // Resolve offset at the slot itself, including the night of a DST transition.
  let utc = localDue;
  for (let i = 0; i < 3; i++) {
    const p = zonedParts(utc, timeZone);
    utc += localDue - Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
  }
  return utc;
}

export function slotAtHour(now, schedule, hour) {
  const fields = zonedParts(now, schedule.timezone);
  const localDue = Date.UTC(fields.year, fields.month - 1, fields.day, hour, schedule.minute);
  return new Date(resolveLocalUtc(localDue, schedule.timezone)).toISOString();
}

export function dueSlot(now, schedule) {
  const fields = zonedParts(now, schedule.timezone);
  const localNow = Date.UTC(fields.year, fields.month - 1, fields.day, fields.hour, fields.minute);
  let localDue;
  for (const hour of schedule.hours) {
    const candidate = Date.UTC(fields.year, fields.month - 1, fields.day, hour, schedule.minute);
    if (candidate <= localNow) localDue = candidate;
  }
  if (localDue === undefined) localDue = Date.UTC(fields.year, fields.month - 1, fields.day - 1, schedule.hours.at(-1), schedule.minute);
  return new Date(resolveLocalUtc(localDue, schedule.timezone)).toISOString();
}

export function romeDay(now, timeZone = 'Europe/Rome') {
  const fields = zonedParts(now, timeZone);
  return `${fields.year}-${String(fields.month).padStart(2, '0')}-${String(fields.day).padStart(2, '0')}`;
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

function instant(value) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : NaN;
}

/** Same rules as scripts/slot_health.py. A v2 edition is judged here even when strict is false. */
export function slotPublished(live, slot, now, {requestId = null, strict = false} = {}) {
  if (!live || typeof live !== 'object') return false;
  if (live.schema_version === 2) {
    if (live.mode !== 'llm' || live.quality?.overall !== 'healthy') return false;
    if (requestId && !(live.request_ids || []).includes(requestId)) return false;
    const due = instant(slot);
    const source = instant(live.source_checked_at);
    const done = instant(live.refresh?.completed_at);
    const generated = instant(live.generated_at);
    if (instant(live.scheduled_slot) !== due) return false;
    if (![due, source, done, generated].every(Number.isFinite)) return false;
    if (!(due <= source && source <= done && done <= now + 60000)) return false;
    if (generated > now + 60000) return false;
    const outcome = live.refresh?.outcome;
    if (outcome === 'generated' && generated < due) return false;
    if (outcome === 'no_change' && live.refresh?.reused_edition_id !== live.edition_id) return false;
    for (const name of ['news', 'sport', 'finance']) {
      const section = live.sections?.[name];
      if (!section || !['healthy', 'unchanged'].includes(section.state) || section.error) return false;
      if (!Array.isArray(section.briefing) || !Array.isArray(section.items)) return false;
      if (section.state === 'unchanged' && !section.reused_edition_id) return false;
      const succeeded = instant(section.last_success_at);
      if (!Number.isFinite(succeeded) || succeeded > done + 60000) return false;
      if (section.state === 'healthy' && succeeded < due) return false;
    }
    const media = live.sections?.media;
    return !!media && ['healthy', 'unchanged', 'skipped'].includes(media.state) && !media.error;
  }
  if (strict) return false;
  const generated = instant(live.generated_at);
  return Number.isFinite(generated) && generated >= instant(slot) && generated <= now + 60000 && live.mode === 'llm';
}

function strictGate(env) {
  return ['1', 'true', 'yes'].includes(String(env.STRICT_SLOT_GATE || '').toLowerCase());
}

export async function tick({now, schedule, state, env, request = fetch, save}) {
  const slot = dueSlot(now, schedule);
  state = state.slot === slot ? {...state} : {slot, attempts:0, phase:'due', nextAttempt:0};
  state.checkedAt = new Date(now).toISOString();
  // A successful dispatch is not publication. Always inspect the live Pages data.
  const live = await json(await request(`${env.BRIEFING_URL}?scheduler=${now}`, {
    cache:'no-store', redirect:'manual', signal:AbortSignal.timeout(10000)
  }));
  if (live.schema_version !== 2) {
    const generated = Date.parse(live.generated_at);
    if (!Number.isFinite(generated)) throw new Error('Live briefing has no valid timestamp');
  }
  state.liveGeneratedAt = live.generated_at ?? null;
  if (slotPublished(live, slot, now, {strict: strictGate(env)})) {
    return {...state, phase:'published', error:null};
  }
  if (!env.GITHUB_TOKEN) return {...state, phase:'needs_github_secret'};
  if (state.nextAttempt > now) return {...state, phase:'waiting'};
  const api = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}`;
  const headers = {Authorization:`Bearer ${env.GITHUB_TOKEN}`, Accept:'application/vnd.github+json',
    'X-GitHub-Api-Version':'2022-11-28', 'User-Agent':'news-briefing-scheduler'};
  const runs = await json(await request(`${api}/runs?branch=main&per_page=20`, {
    headers, redirect:'manual', signal:AbortSignal.timeout(10000)
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
    redirect:'manual', signal:AbortSignal.timeout(10000)
  });
  if (!response.ok) throw new Error(`GitHub dispatch HTTP ${response.status}`);
  if (response.body) await response.body.cancel();
  return {...state,phase:'dispatched'};
}
