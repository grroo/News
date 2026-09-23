// HTTP surface for the owner refresh page, private status, and workflow claim.
import {browserRefreshAllowed, loadAccessKeys, timingSafeEqual, verifyAccessJwt} from './auth.mjs';
import {applyClaim, publicProjection, requestRefresh} from './reservations.mjs';

const PRIVATE_NO_STORE = {'cache-control': 'no-store', 'x-content-type-options': 'nosniff'};

export function ownerPage(pagesUrl) {
  const back = escapeHtml(pagesUrl || '/');
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Fetch new briefing</title>
<meta name="referrer" content="same-origin"></head>
<body>
<main>
<h1>Fetch new briefing</h1>
<p>This starts a paid generation. It is not the public check-for-updates action.</p>
<p><a href="${back}">Return to the briefing</a></p>
<button id="fetch" type="button">Fetch new briefing</button>
<p id="result" role="status"></p>
</main>
<script>
document.getElementById('fetch').onclick = async () => {
  const result = document.getElementById('result');
  result.textContent = 'Reserving…';
  const response = await fetch('/refresh', {
    method: 'POST',
    headers: {'content-type': 'application/json', 'x-requested-with': 'news-refresh'},
    body: JSON.stringify({idempotency_key: crypto.randomUUID()})
  });
  const body = await response.json().catch(() => ({}));
  result.textContent = response.ok ? 'Reserved ' + (body.job_id || '') : (body.error || 'Request was not accepted');
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
    return new Response(ownerPage(env.PAGES_SITE_URL), {status: 200, headers: {...PRIVATE_NO_STORE, 'content-type': 'text/html; charset=utf-8'}});
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
