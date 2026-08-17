// Supabase-issued session verification for the Workshop write path.
// Browse stays anonymous; upload/like/rate/download/delete require a logged-in
// user. The client signs in with Discord via Supabase and sends the resulting
// access token as `Authorization: Bearer <jwt>`. Supabase signs those tokens
// with an ASYMMETRIC key (ES256), so we verify against the project's public
// JWKS — there is no shared secret.
//
// Config:
//   SUPABASE_URL   — project URL; JWKS lives at <url>/auth/v1/.well-known/jwks.json
//   SUPABASE_JWKS  — optional inline JWKS JSON, for local tests only (skips fetch)

const SUPABASE_URL_FALLBACK = "https://upvbmoseimgiathprtsj.supabase.co";
const JWKS_TTL_MS = 10 * 60 * 1000;
const _jwks = { keys: null, at: 0 }; // per-isolate cache

function b64urlToBytes(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  s += "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function b64urlToJson(s) {
  return JSON.parse(new TextDecoder().decode(b64urlToBytes(s)));
}

function jwksUrl(env) {
  const base = (env.SUPABASE_URL || SUPABASE_URL_FALLBACK).replace(/\/+$/, "");
  return `${base}/auth/v1/.well-known/jwks.json`;
}

// Fetch + cache the signing keys. An inline SUPABASE_JWKS (local tests) wins.
// `force` bypasses the cache so a rotated kid can be picked up once.
async function getKeys(env, force = false) {
  if (env.SUPABASE_JWKS) {
    try { return JSON.parse(env.SUPABASE_JWKS).keys || []; } catch { return []; }
  }
  const now = Date.now();
  if (!force && _jwks.keys && now - _jwks.at < JWKS_TTL_MS) return _jwks.keys;
  try {
    const res = await fetch(jwksUrl(env));
    if (!res.ok) return _jwks.keys || [];
    const data = await res.json();
    _jwks.keys = data.keys || [];
    _jwks.at = now;
    return _jwks.keys;
  } catch (e) {
    console.error("JWKS fetch failed", e);
    return _jwks.keys || [];
  }
}

async function verifyEs256(token, env) {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [h, p, sig] = parts;

  let header, payload;
  try { header = b64urlToJson(h); payload = b64urlToJson(p); } catch { return null; }
  if (header.alg !== "ES256" || !header.kid) return null;

  let jwk = (await getKeys(env)).find((k) => k.kid === header.kid);
  if (!jwk) jwk = (await getKeys(env, true)).find((k) => k.kid === header.kid); // maybe rotated
  if (!jwk) return null;

  let ok = false;
  try {
    const key = await crypto.subtle.importKey(
      "jwk", jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"],
    );
    ok = await crypto.subtle.verify(
      { name: "ECDSA", hash: "SHA-256" },
      key,
      b64urlToBytes(sig),                       // JWT ES256 sig is raw r||s (P-1363) — what verify wants
      new TextEncoder().encode(`${h}.${p}`),
    );
  } catch (e) {
    console.error("JWT verify error", e);
    return null;
  }
  if (!ok) return null;
  if (payload.exp && Date.now() / 1000 >= payload.exp) return null;
  return payload;
}

// Normalize a Supabase JWT into the identity the rest of the API uses. Provider-
// neutral: works for email/password (no provider_id/avatar; name from email) and
// OAuth alike. `id` is the Supabase user uuid (claims.sub) — the stable owner key.
function userFromClaims(claims) {
  const m = claims.user_metadata || {};
  const email = claims.email || m.email || null;
  return {
    id: claims.sub,
    email,
    provider_id: m.provider_id || null,
    display_name:
      m.full_name || m.name || m.user_name ||
      (email ? email.split("@")[0] : null) || "Racer",
    avatar_url: m.avatar_url || m.picture || null,
  };
}

// Returns the authenticated user, or null when there's no/!valid token.
export async function getUser(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : null;
  if (!token) return null;
  const claims = await verifyEs256(token, env);
  if (!claims || !claims.sub) return null;
  return userFromClaims(claims);
}

// Upsert the user's profile row so uploads can show display name / avatar
// without re-parsing a token. Cheap; called on write actions.
export async function ensureUser(env, user) {
  await env.DB
    .prepare(
      `INSERT INTO users (id, email, provider_id, display_name, avatar_url, created_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         email = excluded.email,
         provider_id = excluded.provider_id,
         display_name = excluded.display_name,
         avatar_url = excluded.avatar_url`,
    )
    .bind(user.id, user.email, user.provider_id, user.display_name, user.avatar_url, Date.now())
    .run();
}
