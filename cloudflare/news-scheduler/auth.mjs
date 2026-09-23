// Cloudflare Access JWT checks and browser CSRF guards. No secrets are logged.

const textEncoder = new TextEncoder();

export function timingSafeEqual(left, right) {
  const a = textEncoder.encode(String(left ?? ''));
  const b = textEncoder.encode(String(right ?? ''));
  let mismatch = a.length === b.length ? 0 : 1;
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i++) mismatch |= (a[i] || 0) ^ (b[i] || 0);
  return mismatch === 0;
}

function bytesToBase64Url(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function base64UrlToBytes(value) {
  const normalized = String(value).replace(/-/g, '+').replace(/_/g, '/');
  if (normalized.length % 4 === 1) throw new Error('bad base64url');
  const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

const accessKeyCache = new Map();

export async function loadAccessKeys(teamDomain, fetchImpl = fetch, now = Date.now()) {
  const cached = accessKeyCache.get(teamDomain);
  if (cached && cached.until > now) return cached.keys;
  const response = await fetchImpl(`https://${teamDomain}.cloudflareaccess.com/cdn-cgi/access/certs`, {
    redirect: 'manual',
    headers: {accept: 'application/json'},
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) {
    if (response.body) await response.body.cancel();
    throw new Error(`Access certs HTTP ${response.status}`);
  }
  const text = await response.text();
  if (text.length > 65536) throw new Error('Access certs too large');
  const body = JSON.parse(text);
  const keys = Array.isArray(body?.keys) ? body.keys.filter(key => key && key.kty === 'RSA') : [];
  if (!keys.length) throw new Error('Access certs missing');
  accessKeyCache.set(teamDomain, {keys, until: now + 60 * 60 * 1000});
  return keys;
}

export function decodeJwtPart(value) {
  return JSON.parse(new TextDecoder().decode(base64UrlToBytes(value)));
}

export async function signJwt(payload, privateKey, kid = 'test') {
  const header = bytesToBase64Url(textEncoder.encode(JSON.stringify({alg: 'RS256', kid, typ: 'JWT'})));
  const body = bytesToBase64Url(textEncoder.encode(JSON.stringify(payload)));
  const signature = new Uint8Array(await crypto.subtle.sign(
    {name: 'RSASSA-PKCS1-v1_5'}, privateKey, textEncoder.encode(`${header}.${body}`)
  ));
  return `${header}.${body}.${bytesToBase64Url(signature)}`;
}

export async function verifyAccessJwt(token, {teamDomain, audience, ownerEmail, now = Date.now(), keys}) {
  if (!teamDomain || !audience || !ownerEmail) return {ok: false, reason: 'not_configured'};
  const parts = String(token || '').split('.');
  if (parts.length !== 3 || parts.some(part => !part)) return {ok: false, reason: 'malformed'};
  let header, payload;
  try {
    header = decodeJwtPart(parts[0]);
    payload = decodeJwtPart(parts[1]);
  } catch {
    return {ok: false, reason: 'malformed'};
  }
  if (header.alg !== 'RS256') return {ok: false, reason: 'malformed'};
  const jwk = (keys || []).find(key => key.kty === 'RSA' && (!header.kid || key.kid === header.kid)) || (keys || []).find(key => key.kty === 'RSA');
  if (!jwk) return {ok: false, reason: 'malformed'};
  let signature;
  try {
    signature = base64UrlToBytes(parts[2]);
  } catch {
    return {ok: false, reason: 'forged'};
  }
  let key;
  try {
    key = await crypto.subtle.importKey('jwk', jwk, {name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256'}, false, ['verify']);
  } catch {
    return {ok: false, reason: 'malformed'};
  }
  let valid = false;
  try {
    valid = await crypto.subtle.verify(
      {name: 'RSASSA-PKCS1-v1_5'}, key, signature, textEncoder.encode(`${parts[0]}.${parts[1]}`)
    );
  } catch {
    return {ok: false, reason: 'forged'};
  }
  if (!valid) return {ok: false, reason: 'forged'};
  const issuer = `https://${teamDomain}.cloudflareaccess.com`;
  if (payload.iss !== issuer) return {ok: false, reason: 'issuer'};
  const audiences = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!audiences.includes(audience)) return {ok: false, reason: 'audience'};
  if (typeof payload.exp !== 'number' || payload.exp * 1000 <= now) return {ok: false, reason: 'expired'};
  if (typeof payload.nbf === 'number' && payload.nbf * 1000 > now) return {ok: false, reason: 'expired'};
  // Service tokens authenticate an application, not the owner.
  if (payload.common_name && !payload.email) return {ok: false, reason: 'service_token'};
  if (String(payload.email || '').toLowerCase() !== String(ownerEmail).toLowerCase()) return {ok: false, reason: 'owner'};
  return {ok: true, email: payload.email};
}

export function browserRefreshAllowed(request) {
  const origin = request.headers.get('origin');
  let expected;
  try {
    expected = new URL(request.url).origin;
  } catch {
    return false;
  }
  if (!origin || origin !== expected) return false;
  if (request.headers.get('x-requested-with') !== 'news-refresh') return false;
  const type = request.headers.get('content-type') || '';
  return type.toLowerCase().startsWith('application/json');
}
