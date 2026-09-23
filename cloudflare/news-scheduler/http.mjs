// HTTP surface for the owner refresh page, private status, and workflow claim.
import {browserRefreshAllowed, loadAccessKeys, timingSafeEqual, verifyAccessJwt} from './auth.mjs';
import {applyClaim, publicProjection, requestRefresh} from './reservations.mjs';

const PRIVATE_NO_STORE = {'cache-control': 'no-store', 'x-content-type-options': 'nosniff'};

export function safeReturnUrl(candidate, pagesSiteUrl) {
  let allowed;
  try {
    allowed = new URL(pagesSiteUrl);
  } catch {
    return '';
  }
  if (allowed.protocol !== 'https:' && allowed.protocol !== 'http:') return '';
  if (allowed.username || allowed.password) return '';
  if (!candidate) return allowed.href;
  let target;
  try {
    target = new URL(candidate, allowed);
  } catch {
    return '';
  }
  if (target.protocol !== allowed.protocol || target.origin !== allowed.origin) return '';
  if (target.username || target.password) return '';
  return target.href;
}

function scriptJson(value) {
  return JSON.stringify(value).replace(/</g, '\\u003c');
}

const SAFE_REFRESH_ERRORS = new Set(['failed', 'expired', 'rate_limited', 'daily_limit', 'concurrent_job']);

export function refreshErrorCode(body) {
  if (!body || typeof body !== 'object') return '';
  if (body.status === 'failed' || body.status === 'expired') return body.status;
  if (typeof body.error === 'string' && SAFE_REFRESH_ERRORS.has(body.error)) return body.error;
  return '';
}

export function ownerReturnHref(returnUrl, body = {}) {
  if (!returnUrl) return '';
  let url;
  try {
    url = new URL(returnUrl);
  } catch {
    return '';
  }
  if (body.request_id) url.searchParams.set('request', String(body.request_id));
  if (body.job_id) url.searchParams.set('job', String(body.job_id));
  const code = refreshErrorCode(body);
  if (code) url.searchParams.set('refresh_error', code);
  if (typeof body.retry_after === 'string' && body.retry_after) url.searchParams.set('retry_after', body.retry_after);
  return url.href;
}

export function ownerPage(pagesUrl, returnCandidate) {
  const back = safeReturnUrl(returnCandidate, pagesUrl) || safeReturnUrl('', pagesUrl);
  const href = escapeHtml(back || '/');
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Fetch new briefing</title>
<meta name="referrer" content="same-origin"></head>
<body>
<main>
<h1>Fetch new briefing</h1>
<p>This starts a paid generation. It is not the public check-for-updates action.</p>
<p><a id="back" href="${href}">Return to the briefing</a></p>
<button id="fetch" type="button">Fetch new briefing</button>
<p id="result" role="status"></p>
</main>
<script>
const RETURN_URL = ${scriptJson(back || '')};
const TERMINAL = new Set(['succeeded', 'no_change', 'degraded', 'failed', 'expired']);
const LABELS = {accepted:'Request received', queued:'Waiting', building:'Building', publishing:'Publishing', succeeded:'Published', no_change:'Nothing new to publish', degraded:'Published with older sections', failed:'Could not complete', expired:'Expired', rate_limited:'Too many requests', daily_limit:'Daily limit reached', concurrent_job:'A fetch is already in progress'};
function described(body) {
  const label = LABELS[body.status] || LABELS[body.error] || body.error || 'Request was not accepted';
  return body.request_id ? label + ' ' + body.request_id : label;
}
function returnHref(body) {
  if (!RETURN_URL) return '';
  const url = new URL(RETURN_URL);
  if (body.request_id) url.searchParams.set('request', body.request_id);
  if (body.job_id) url.searchParams.set('job', body.job_id);
  const safe = new Set(['failed', 'expired', 'rate_limited', 'daily_limit', 'concurrent_job']);
  let code = '';
  if (body.status === 'failed' || body.status === 'expired') code = body.status;
  else if (safe.has(body.error)) code = body.error;
  if (code) url.searchParams.set('refresh_error', code);
  if (body.retry_after) url.searchParams.set('retry_after', body.retry_after);
  return url.href;
}
function showReturn(body) {
  const href = returnHref(body);
  const link = document.getElementById('back');
  if (href && link) link.href = href;
  return href;
}
async function poll(jobId) {
  const result = document.getElementById('result');
  for (let attempt = 0; attempt < 600; attempt++) {
    await new Promise(resolve => setTimeout(resolve, attempt ? 2000 : 300));
    const response = await fetch('/refresh/' + encodeURIComponent(jobId), {cache: 'no-store'});
    const body = await response.json().catch(() => ({}));
    result.textContent = described(body);
    const href = showReturn(body);
    if (TERMINAL.has(body.status) && href) {
      location.assign(href);
      return;
    }
    if (!response.ok) return;
  }
}
document.getElementById('fetch').onclick = async () => {
  const result = document.getElementById('result');
  result.textContent = 'Reserving…';
  const response = await fetch('/refresh', {
    method: 'POST',
    headers: {'content-type': 'application/json', 'x-requested-with': 'news-refresh'},
    body: JSON.stringify({idempotency_key: crypto.randomUUID()})
  });
  const body = await response.json().catch(() => ({}));
  result.textContent = described(body.status ? body : {...body, status: body.error});
  const href = showReturn(body);
  if (!response.ok) {
    if (href && body.error) location.assign(href);
    return;
  }
  if (body.job_id) await poll(body.job_id);
};
</script>
</body></html>`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
}

function json(body, status, extra = {}) {
  return new Response(JSON.stringify(body), {status, headers: {...PRIVATE_NO_STORE, 'content-type': 'application/json; charset=utf-8', ...extra}});
}

async function accessOk(request, env, deps) {
  if (env.REFRESH_ENABLED !== 'true') return {ok: false, status: 404, reason: 'disabled'};
  if (!env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD || !env.OWNER_EMAIL) return {ok: false, status: 503, reason: 'not_configured'};
  const token = request.headers.get('cf-access-jwt-assertion');
  if (!token) return {ok: false, status: 401, reason: 'unauthorized'};
  let keys = deps.keys;
  if (!deps.verify && !keys) {
    try {
      keys = await loadAccessKeys(env.ACCESS_TEAM_DOMAIN, deps.fetchCerts || fetch, deps.now);
    } catch {
      return {ok: false, status: 503, reason: 'identity_unavailable'};
    }
  }
  const verified = deps.verify
    ? await deps.verify(token)
    : await verifyAccessJwt(token, {
      teamDomain: env.ACCESS_TEAM_DOMAIN,
      audience: env.ACCESS_AUD,
      ownerEmail: env.OWNER_EMAIL,
      now: deps.now,
      keys,
    });
  if (!verified.ok) return {ok: false, status: 401, reason: verified.reason || 'unauthorized'};
  return {ok: true, email: verified.email};
}

export async function handleRequest(request, env, deps) {
  const url = new URL(request.url);
  const path = url.pathname;
  if (request.method === 'OPTIONS') return new Response(null, {status: 403, headers: PRIVATE_NO_STORE});
  if (request.method === 'GET' && path === '/health') {
    const state = await deps.publicStatus();
    return json({configured: !!env.GITHUB_TOKEN, schedule: deps.schedule, ...state}, 200);
  }
  if (request.method === 'POST' && path === '/internal/reservations/claim') {
    const presented = (request.headers.get('authorization') || '').replace(/^Bearer\s+/i, '');
    const expected = env.NEWS_RESERVATION_GATE_TOKEN || '';
    if (!expected || !presented || !timingSafeEqual(presented, expected)) return json({error: 'unauthorized'}, 401);
    let claim;
    try {
      claim = await request.json();
    } catch {
      return json({error: 'invalid_json'}, 400);
    }
    if (claim.repository !== env.GITHUB_REPO || claim.workflow !== env.GITHUB_WORKFLOW || claim.ref !== 'refs/heads/main') {
      return json({decision: 'denied', reservation_id: claim.reservation_id || '', request_id: claim.request_id || '', reason: 'scope_mismatch'}, 200);
    }
    const result = await deps.claim(claim);
    return json({decision: result.decision, reservation_id: result.reservation_id, request_id: result.request_id, ...(result.reason ? {reason: result.reason} : {})}, 200);
  }
  if (path === '/' && request.method === 'GET') {
    const access = await accessOk(request, env, deps);
    if (!access.ok) return json({error: access.reason}, access.status);
    return new Response(ownerPage(env.PAGES_SITE_URL, url.searchParams.get('return')), {status: 200, headers: {...PRIVATE_NO_STORE, 'content-type': 'text/html; charset=utf-8'}});
  }
  if (path === '/refresh' && request.method === 'GET') return json({error: 'not_found'}, 404);
  if (request.method === 'POST' && path === '/refresh') {
    const access = await accessOk(request, env, deps);
    if (!access.ok) return json({error: access.reason}, access.status);
    if (!browserRefreshAllowed(request)) return json({error: 'cross_site'}, 403);
    let body;
    try {
      body = await request.json();
    } catch {
      return json({error: 'invalid_json'}, 400);
    }
    const idempotencyKey = request.headers.get('idempotency-key') || body.idempotency_key;
    const prepared = await deps.refresh({idempotencyKey, trigger: 'manual'});
    if (prepared.dispatch) {
      try {
        await deps.dispatchWorkflow(prepared.dispatch);
      } catch {
        // The reservation was stored first. Reconciliation must see the ambiguous dispatch.
      }
    }
    return json(prepared.body, prepared.http);
  }
  const statusMatch = path.match(/^\/refresh\/([^/]+)$/);
  if (statusMatch && request.method === 'GET') {
    const access = await accessOk(request, env, deps);
    if (!access.ok) return json({error: access.reason}, access.status);
    const job = await deps.readJob(decodeURIComponent(statusMatch[1]));
    if (!job) return json({error: 'not_found'}, 404);
    const body = {
      job_id: job.job_id,
      request_id: job.request_id,
      status: job.status,
      ...(job.edition_id ? {edition_id: job.edition_id} : {}),
      ...(job.published_edition_id ? {published_edition_id: job.published_edition_id} : {}),
      ...(job.scheduled_slot ? {scheduled_slot: job.scheduled_slot} : {}),
      ...(job.error ? {error: job.error} : {}),
    };
    return json(body, 200);
  }
  return json({error: 'not_found'}, 404);
}

export function attachRefresh(state, input) {
  return requestRefresh(state, input);
}

export function attachClaim(state, claim, now) {
  return applyClaim(state, claim, now);
}

export {publicProjection};
