// Durable Object decisions for reservations, limits, claims and publication.
// Persistence is the caller's job; these functions only mutate the state object
// they are given, and they do it before any dispatch is attempted.
import {dueSlot, romeDay, slotAtHour, slotPublished} from './core.mjs';

export const SCOPE = {repository: 'grroo/News', workflow: 'build.yml', ref: 'refs/heads/main'};
export const MANUAL_COOLDOWN_MS = 15 * 60 * 1000;
export const MANUAL_PER_DAY = 5;
export const GENERATION_PER_DAY = 8;
export const CLAIM_LEASE_MS = 15 * 60 * 1000;
export const PUBLICATION_TIMEOUT_MS = 20 * 60 * 1000;

const ACTIVE = new Set(['accepted', 'queued', 'building', 'publishing']);

export function emptyControl(slot = '') {
  return {
    schema_version: 2,
    slot,
    phase: 'due',
    attempts: 0,
    nextAttempt: 0,
    checkedAt: null,
    liveGeneratedAt: null,
    runId: null,
    error: null,
    daily: {rome_day: '', manual_count: 0, generation_count: 0},
    reservation: null,
    reservations: {},
    jobs: {},
    idempotency: {},
    slot_first_attempt: {},
    active_job_id: null,
    dispatch_ambiguous: false,
    deploy_only_for: null,
    manual_cooldown_until: 0,
  };
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function instant(value) {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) throw new Error('Timezone required');
  return parsed;
}

function iso(ms) {
  return new Date(ms).toISOString();
}

function randomId(prefix) {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return prefix + [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function rollDay(state, now, timeZone) {
  const day = romeDay(now, timeZone);
  if (state.daily?.rome_day !== day) {
    state.daily = {rome_day: day, manual_count: 0, generation_count: 0};
    state.slot_first_attempt = {};
  }
}

function nextRomeMidnight(now, timeZone) {
  const day = romeDay(now, timeZone);
  const [year, month, date] = day.split('-').map(Number);
  const guess = Date.UTC(year, month - 1, date + 1, 0, 0);
  // Step until the Rome calendar date advances; DST makes the UTC offset uneven.
  for (let cursor = guess - 3 * 3600 * 1000; cursor < guess + 3 * 3600 * 1000; cursor += 60 * 1000) {
    if (romeDay(cursor, timeZone) !== day && romeDay(cursor - 60 * 1000, timeZone) === day) return iso(cursor);
  }
  return iso(guess);
}

export function unattemptedFutureSlots(state, now, schedule) {
  const due = dueSlot(now, schedule);
  let count = 0;
  for (const hour of schedule.hours) {
    const slot = slotAtHour(now, schedule, hour);
    if (state.slot_first_attempt?.[slot]) continue;
    if (Date.parse(slot) > now || slot === due) count += 1;
  }
  return count;
}

function capacityOk(state, now, schedule, kind) {
  const future = unattemptedFutureSlots(state, now, schedule);
  // future already includes the current due slot when it has not been attempted.
  // A slot's first scheduled attempt may use that slot's reserved run. Manual
  // requests and retries must leave the remaining unattempted slots untouched.
  const reserved = kind === 'scheduled_first' ? Math.max(0, future - 1) : future;
  return state.daily.generation_count + 1 + reserved <= GENERATION_PER_DAY;
}

function limit(error, retryAfter) {
  return {ok: false, http: 429, body: {error, retry_after: retryAfter}, dispatch: null};
}

function jobView(job) {
  return {
    job_id: job.job_id,
    request_id: job.request_id,
    status: job.status,
    status_url: `/refresh/${job.job_id}`,
    idempotency_key: job.idempotency_key,
    ...(job.edition_id ? {edition_id: job.edition_id} : {}),
    ...(job.published_edition_id ? {published_edition_id: job.published_edition_id} : {}),
    ...(job.scheduled_slot ? {scheduled_slot: job.scheduled_slot} : {}),
    ...(job.error ? {error: job.error} : {}),
  };
}

function activeJob(state) {
  const job = state.jobs[state.active_job_id];
  return job && ACTIVE.has(job.status) ? job : null;
}

export function claimDecision(reservation, claim, now) {
  if (!validReservation(reservation)) return {decision: 'denied', reservation_id: claim?.reservation_id || '', request_id: claim?.request_id || '', reason: 'unknown'};
  if (!validClaim(claim)) return {decision: 'denied', reservation_id: reservation.reservation_id, request_id: claim?.request_id || reservation.request_id, reason: 'scope_mismatch'};
  const response = {decision: 'denied', reservation_id: claim.reservation_id, request_id: claim.request_id};
  if (reservation.reservation_id !== claim.reservation_id) return {...response, reason: 'unknown'};
  if (reservation.request_id !== claim.request_id) return {...response, reason: 'request_mismatch'};
  if (reservation.scope.repository !== claim.repository || reservation.scope.workflow !== claim.workflow || reservation.scope.ref !== claim.ref) {
    return {...response, reason: 'scope_mismatch'};
  }
  if (reservation.status === 'cancelled') return {...response, reason: 'cancelled'};
  if (reservation.status === 'claimed') {
    if (reservation.claimed_run_id === claim.run_id && reservation.claimed_run_attempt === claim.run_attempt) {
      return {decision: 'allowed', reservation_id: claim.reservation_id, request_id: claim.request_id};
    }
    return {...response, reason: 'claimed_by_other'};
  }
  if (reservation.status !== 'reserved') return {...response, reason: 'expired'};
  if (instant(now) >= instant(reservation.expires_at)) return {...response, reason: 'expired'};
  return {decision: 'allowed', reservation_id: claim.reservation_id, request_id: claim.request_id};
}

function validReservation(reservation) {
  if (!reservation || typeof reservation !== 'object') return false;
  for (const key of ['reservation_id', 'request_id', 'idempotency_key', 'created_at', 'expires_at']) {
    if (typeof reservation[key] !== 'string' || !reservation[key]) return false;
  }
  if (!Array.isArray(reservation.request_ids) || reservation.request_ids.length === 0) return false;
  if (!['manual', 'scheduled', 'workflow_dispatch'].includes(reservation.trigger)) return false;
  if (reservation.trigger === 'scheduled' && !reservation.scheduled_slot) return false;
  if (!reservation.scope || reservation.scope.repository !== SCOPE.repository || reservation.scope.workflow !== SCOPE.workflow || reservation.scope.ref !== SCOPE.ref) return false;
  if (!['reserved', 'claimed', 'expired', 'cancelled'].includes(reservation.status)) return false;
  if (reservation.status === 'claimed') {
    if (!reservation.claimed_at || !Number.isInteger(reservation.claimed_run_id) || reservation.claimed_run_id < 1) return false;
    if (!Number.isInteger(reservation.claimed_run_attempt) || reservation.claimed_run_attempt < 1) return false;
  }
  return true;
}

function validClaim(claim) {
  if (!claim || typeof claim !== 'object') return false;
  for (const key of ['reservation_id', 'request_id', 'repository', 'workflow', 'ref']) {
    if (typeof claim[key] !== 'string' || !claim[key]) return false;
  }
  if (!Number.isInteger(claim.run_id) || claim.run_id < 1) return false;
  if (!Number.isInteger(claim.run_attempt) || claim.run_attempt < 1) return false;
  return claim.repository === SCOPE.repository && claim.workflow === SCOPE.workflow && claim.ref === SCOPE.ref;
}

export function applyClaim(state, claim, now) {
  const stored = state.reservations?.[claim?.reservation_id] || null;
  if (!stored) {
    return {decision: 'denied', reservation_id: claim?.reservation_id || '', request_id: claim?.request_id || '', reason: 'unknown', state};
  }
  const decision = claimDecision(stored, claim, now);
  if (decision.decision === 'allowed' && stored.status === 'reserved') {
    stored.status = 'claimed';
    stored.claimed_at = now;
    stored.claimed_run_id = claim.run_id;
    stored.claimed_run_attempt = claim.run_attempt;
    state.phase = 'build_in_progress';
    state.runId = claim.run_id;
    state.reservation = stored;
    for (const job of Object.values(state.jobs)) {
      if (job.reservation_id === stored.reservation_id && ACTIVE.has(job.status)) job.status = 'publishing';
    }
  }
  return {...decision, state};
}

function openReservation(state) {
  const reservation = state.reservation;
  if (!reservation || !['reserved', 'claimed'].includes(reservation.status)) return null;
  if (!activeJob(state) && reservation.status !== 'reserved' && reservation.status !== 'claimed') return null;
  return reservation;
}

export function requestRefresh(state, {now, schedule, idempotencyKey, trigger = 'manual', ids = undefined}) {
  state = clone(state.schema_version ? state : emptyControl());
  const timeZone = schedule.timezone;
  rollDay(state, now, timeZone);
  if (!idempotencyKey || typeof idempotencyKey !== 'string') {
    return {ok: false, http: 400, body: {error: 'idempotency_key_required'}, dispatch: null, state};
  }
  const existingId = state.idempotency[idempotencyKey];
  if (existingId && state.jobs[existingId]) {
    return {ok: true, http: 200, body: jobView({...state.jobs[existingId], status: state.jobs[existingId].status === 'accepted' ? 'accepted' : state.jobs[existingId].status}), dispatch: null, state};
  }
  const current = activeJob(state);
  const reservation = openReservation(state);
  if (current && reservation && state.deploy_only_for !== reservation.reservation_id) {
    // The workflow was already dispatched with reservation.request_id. A second
    // click joins that published identity so Pages can acknowledge it.
    const requestId = reservation.request_id;
    const job = {
      job_id: ids?.job_id || randomId('job_'),
      request_id: requestId,
      status: current.status === 'accepted' ? 'queued' : current.status,
      idempotency_key: idempotencyKey,
      reservation_id: reservation.reservation_id,
      trigger,
      scheduled_slot: reservation.scheduled_slot || null,
      created_at: iso(now),
    };
    state.jobs[job.job_id] = job;
    state.idempotency[idempotencyKey] = job.job_id;
    state.reservations[reservation.reservation_id] = reservation;
    return {ok: true, http: 200, body: {...jobView(job), status: 'accepted'}, dispatch: null, state, coalesced: true};
  }
  if (current && state.deploy_only_for) return {...limit('concurrent_job', iso(now + MANUAL_COOLDOWN_MS)), state};
  if (trigger === 'manual' && state.manual_cooldown_until > now) {
    return {...limit('rate_limited', iso(state.manual_cooldown_until)), state};
  }
  if (trigger === 'manual' && state.daily.manual_count >= MANUAL_PER_DAY) {
    return {...limit('daily_limit', nextRomeMidnight(now, timeZone)), state};
  }
  const kind = trigger === 'scheduled' && !state.slot_first_attempt?.[dueSlot(now, schedule)] ? 'scheduled_first' : trigger === 'scheduled' ? 'scheduled_retry' : 'manual';
  if (!capacityOk(state, now, schedule, kind)) return {...limit('daily_limit', nextRomeMidnight(now, timeZone)), state};
  const due = dueSlot(now, schedule);
  if (state.slot !== due) {
    state.slot = due;
    state.attempts = 0;
  }
  const slot = trigger === 'scheduled' ? due : undefined;
  const requestId = ids?.request_id || randomId('req_');
  const reservationId = ids?.reservation_id || randomId('res_');
  const jobId = ids?.job_id || randomId('job_');
  const created = iso(now);
  const createdReservation = {
    reservation_id: reservationId,
    request_id: requestId,
    request_ids: [requestId],
    idempotency_key: idempotencyKey,
    trigger,
    ...(slot ? {scheduled_slot: slot} : {}),
    created_at: created,
    expires_at: iso(now + CLAIM_LEASE_MS),
    scope: {...SCOPE},
    status: 'reserved',
  };
  const job = {
    job_id: jobId,
    request_id: requestId,
    status: 'queued',
    idempotency_key: idempotencyKey,
    reservation_id: reservationId,
    trigger,
    scheduled_slot: slot || null,
    created_at: created,
  };
  state.reservations[reservationId] = createdReservation;
  state.reservation = createdReservation;
  state.jobs[jobId] = job;
  state.idempotency[idempotencyKey] = jobId;
  state.active_job_id = jobId;
  state.daily.generation_count += 1;
  if (trigger === 'manual') {
    state.daily.manual_count += 1;
    state.manual_cooldown_until = now + MANUAL_COOLDOWN_MS;
  }
  if (trigger === 'scheduled' && slot) state.slot_first_attempt[slot] = true;
  state.phase = 'dispatching';
  state.slot = due;
  if (trigger === 'scheduled') state.attempts += 1;
  state.nextAttempt = now + MANUAL_COOLDOWN_MS;
  state.dispatch_ambiguous = true;
  state.error = null;
  state.checkedAt = created;
  const inputs = {mock: false, reservation_id: reservationId, request_id: requestId, deploy_only: false};
  if (slot) inputs.scheduled_slot = slot;
  return {
    ok: true,
    http: 202,
    body: {...jobView(job), status: 'accepted'},
    dispatch: {ref: 'main', inputs},
    state,
  };
}

export function noteDispatchResult(state, outcome) {
  state = clone(state);
  if (outcome === 'accepted') {
    state.phase = 'dispatched';
    state.dispatch_ambiguous = true;
    const job = state.jobs[state.active_job_id];
    if (job && ACTIVE.has(job.status)) job.status = 'building';
  } else {
    state.phase = 'dispatching';
    state.dispatch_ambiguous = true;
    state.error = 'dispatch_timeout';
  }
  return state;
}

function runMentions(run, requestId) {
  if (!requestId) return false;
  return `${run.name || ''} ${run.display_title || ''}`.includes(requestId);
}

function sameRun(run, reservation) {
  const claimed = reservation?.claimed_run_id;
  if (!Number.isInteger(claimed) || claimed < 1) return false;
  return Number(run?.id) === claimed;
}

function unconfirmed(state, job, live) {
  finishJob(state, job, 'expired', live);
  state.phase = 'attention_needed';
  state.attempts = Math.max(state.attempts || 0, 3);
  state.error = 'Publication was not confirmed';
  state.dispatch_ambiguous = false;
  return {state, dispatch: null};
}

export function reconcile(state, {now, schedule, runs, runsListed, live}) {
  state = clone(state);
  rollDay(state, now, schedule.timezone);
  state.checkedAt = iso(now);
  const reservation = state.reservation;
  const job = activeJob(state);
  if (live && Number.isFinite(Date.parse(live.generated_at))) state.liveGeneratedAt = live.generated_at;
  if (job && live) {
    const status = publicationFor(live, job.request_id);
    if (status) {
      finishJob(state, job, status, live);
      if (reservation) {
        for (const extra of Object.values(state.jobs)) {
          if (extra.job_id === job.job_id || extra.reservation_id !== reservation.reservation_id || !ACTIVE.has(extra.status)) continue;
          const extraStatus = publicationFor(live, extra.request_id);
          if (extraStatus) finishJob(state, extra, extraStatus, live);
        }
      }
      return {state, dispatch: null};
    }
  }
  if (!reservation || !job) return {state, dispatch: null};
  const started = instant(reservation.created_at);
  const listed = runsListed ? (runs || []) : [];
  const named = listed.filter(run => {
    const created = Date.parse(run.created_at);
    return runMentions(run, reservation.request_id) && Number.isFinite(created) && created >= started - 5000;
  });
  const claimed = listed.find(run => sameRun(run, reservation));
  const chosen = claimed || named[0] || null;
  if (chosen) {
    state.runId = chosen.id;
    state.phase = chosen.status === 'completed' ? state.phase : 'build_in_progress';
    state.dispatch_ambiguous = false;
    job.status = 'publishing';
    job.run_id = chosen.id;
    if (chosen.status === 'completed' && chosen.conclusion && chosen.conclusion !== 'success') {
      finishJob(state, job, 'failed', live);
      state.phase = 'due';
      state.error = 'Workflow failed';
      state.dispatch_ambiguous = false;
      return {state, dispatch: null, retry: state.attempts < 3};
    }
    if (chosen.status === 'completed' && chosen.conclusion === 'success' && state.deploy_only_for !== reservation.reservation_id) {
      state.deploy_only_for = reservation.reservation_id;
      return {state, dispatch: {ref: 'main', inputs: {mock: false, deploy_only: true, reservation_id: reservation.reservation_id, request_id: reservation.request_id, ...(reservation.scheduled_slot ? {scheduled_slot: reservation.scheduled_slot} : {})}}};
    }
    if (now >= started + PUBLICATION_TIMEOUT_MS) return unconfirmed(state, job, live);
    return {state, dispatch: null};
  }
  // A partial run list, including the first 20 workflow_dispatch rows, does not
  // prove this reservation has no run. Leave it unresolved until publication
  // or the confirmation window. Do not refund and do not dispatch again.
  if (now >= started + PUBLICATION_TIMEOUT_MS) return unconfirmed(state, job, live);
  state.phase = 'waiting';
  return {state, dispatch: null};
}

function finishJob(state, job, status, live) {
  job.status = status;
  // All coalesced jobs share the reservation's exact publication identity.
  // Complete them on failure/expiry as well as successful publication.
  for (const other of Object.values(state.jobs)) {
    if (other.job_id !== job.job_id && other.reservation_id === job.reservation_id && ACTIVE.has(other.status)
        && (['failed', 'expired'].includes(status) || publicationFor(live, other.request_id) === status)) {
      other.status = status;
      if (live?.request_ids?.includes(other.request_id) && live.edition_id) {
        other.edition_id = live.edition_id;
        other.published_edition_id = live.edition_id;
      }
      if (status === 'expired') other.error = 'Publication was not confirmed';
    }
  }
  if (live?.request_ids?.includes(job.request_id) && live.edition_id) {
    job.edition_id = live.edition_id;
    job.published_edition_id = live.edition_id;
  }
  if (status === 'expired') job.error = 'Publication was not confirmed';
  if (state.active_job_id === job.job_id && !ACTIVE.has(status)) {
    state.active_job_id = null;
    if (state.reservation?.reservation_id === job.reservation_id) state.reservation = null;
    state.dispatch_ambiguous = false;
    state.deploy_only_for = null;
  }
}

export function publicationFor(edition, requestId) {
  if (!edition || edition.schema_version !== 2 || !Array.isArray(edition.request_ids)) return null;
  if (!edition.request_ids.includes(requestId)) return null;
  const quality = edition.quality?.overall;
  const outcome = edition.refresh?.outcome;
  if (quality === 'failed') return 'failed';
  if (quality === 'degraded') return 'degraded';
  if (outcome === 'no_change') return 'no_change';
  if (quality === 'healthy' && outcome === 'generated') return 'succeeded';
  return null;
}

export function satisfiesHealthySlot(edition, slot, now, requestId) {
  if (!edition || edition.schema_version !== 2) return false;
  if (edition.mode !== 'llm' || edition.quality?.overall !== 'healthy') return false;
  if (requestId && !edition.request_ids?.includes(requestId)) return false;
  try {
    const due = instant(slot);
    if (instant(edition.scheduled_slot) !== due) return false;
    const source = instant(edition.source_checked_at);
    const done = instant(edition.refresh.completed_at);
    const generated = instant(edition.generated_at);
    if (!(due <= source && source <= done && done <= now + 60000)) return false;
    if (generated > now + 60000) return false;
    if (edition.refresh.outcome === 'generated' && generated < due) return false;
    if (edition.refresh.outcome === 'no_change' && edition.refresh.reused_edition_id !== edition.edition_id) return false;
    for (const name of ['news', 'sport', 'finance']) {
      const section = edition.sections[name];
      if (!section || !['healthy', 'unchanged'].includes(section.state) || section.error) return false;
      if (!Array.isArray(section.briefing) || !Array.isArray(section.items)) return false;
      if (section.state === 'unchanged' && !section.reused_edition_id) return false;
      const succeeded = instant(section.last_success_at);
      if (succeeded > done + 60000) return false;
      if (section.state === 'healthy' && succeeded < due) return false;
    }
    const media = edition.sections.media;
    return !!media && ['healthy', 'unchanged', 'skipped'].includes(media.state) && !media.error;
  } catch {
    return false;
  }
}

export function advanceScheduled(state, {now, schedule, runs, runsListed, live, strict = false, tokenPresent = true, idempotencyKey = undefined}) {
  state = clone(state.schema_version ? state : emptyControl());
  if (!live || !Number.isFinite(Date.parse(live.generated_at))) throw new Error('Live briefing has no valid timestamp');
  const slot = dueSlot(now, schedule);
  if (state.slot !== slot) {
    state = {...state, slot, attempts: 0, nextAttempt: 0, dispatch_ambiguous: false, phase: 'due', error: null};
    if (state.reservation && !['reserved', 'claimed'].includes(state.reservation.status)) state.reservation = null;
  }
  state.checkedAt = iso(now);
  const requestId = state.reservation?.request_id;
  // T04's migration contract: v2 always uses section health; strict only
  // disables the legacy fallback. Use the same decision as the legacy cron.
  const published = slotPublished(live, slot, now, {requestId, strict});
  if (published) {
    state.phase = 'published';
    state.error = null;
    state.liveGeneratedAt = live.generated_at;
    const job = activeJob(state);
    if (job && live) {
      const status = publicationFor(live, job.request_id);
      if (status) finishJob(state, job, status, live);
    }
    return {state, dispatch: null};
  }
  if (live && Number.isFinite(Date.parse(live.generated_at))) state.liveGeneratedAt = live.generated_at;
  if (!tokenPresent) {
    state.phase = 'needs_github_secret';
    return {state, dispatch: null};
  }
  if (activeJob(state)) {
    const reconciled = reconcile(state, {now, schedule, runs, runsListed, live});
    if (reconciled.retry) {
      return requestRefresh(reconciled.state, {now, schedule, trigger: 'scheduled', idempotencyKey: idempotencyKey || `scheduled:${slot}:retry:${reconciled.state.attempts + 1}`});
    }
    return reconciled;
  }
  if (state.attempts >= 3) {
    state.phase = 'attention_needed';
    state.error = 'Three attempts without a published briefing';
    return {state, dispatch: null};
  }
  return requestRefresh(state, {now, schedule, trigger: 'scheduled', idempotencyKey: idempotencyKey || `scheduled:${slot}:${state.attempts + 1}`});
}

export function publicProjection(state) {
  return {
    phase: state.phase || 'not_checked',
    slot: state.slot || null,
    attempts: state.attempts || 0,
    checkedAt: state.checkedAt || null,
    liveGeneratedAt: state.liveGeneratedAt || null,
    runId: state.runId || null,
    error: state.error || null,
    nextAttempt: state.nextAttempt || 0,
  };
}
