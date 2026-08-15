// Supabase-issued session verification for the Workshop write path.
// Browse stays anonymous; upload/like/rate/download/delete require a logged-in
// user. The client signs in with Discord via Supabase and sends the resulting
// access token as `Authorization: Bearer <jwt>`. We verify it here (HS256 with
// the project's JWT secret) and resolve a stable user identity from the claims.
//
// Env: SUPABASE_JWT_SECRET — the project's JWT secret (Supabase → Settings → API).
// Set as a secret, never in source: `wrangler pages secret put SUPABASE_JWT_SECRET`.
//
// (If the Supabase project uses asymmetric signing keys instead of the shared
// secret, swap verifyHs256 for a JWKS fetch+verify — the getUser contract is the
// same. HS256 covers the default/legacy JWT-secret setup.)

function b64urlToBytes(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  s += "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function verifyHs256(token, secret) {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [h, p, sig] = parts;

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const ok = await crypto.subtle.verify(
    "HMAC",
    key,
    b64urlToBytes(sig),
    new TextEncoder().encode(`${h}.${p}`),
  );
  if (!ok) return null;

  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(b64urlToBytes(p)));
  } catch {
    return null;
  }
  // Reject expired tokens (exp is seconds since epoch).
  if (payload.exp && Date.now() / 1000 >= payload.exp) return null;
  return payload;
}

// Normalize a Supabase JWT into the identity the rest of the API uses.
// `id` is the Supabase user uuid (claims.sub) — the stable owner key.
function userFromClaims(claims) {
  const m = claims.user_metadata || {};
  return {
    id: claims.sub,
    discord_id: m.provider_id || m.sub || null,
    display_name: m.full_name || m.name || m.user_name || "Racer",
    avatar_url: m.avatar_url || null,
  };
}

// Returns the authenticated user, or null when there's no/!valid token.
export async function getUser(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : null;
  if (!token) return null;
  if (!env.SUPABASE_JWT_SECRET) {
    console.error("SUPABASE_JWT_SECRET not configured — cannot verify sessions");
    return null;
  }
  const claims = await verifyHs256(token, env.SUPABASE_JWT_SECRET);
  if (!claims || !claims.sub) return null;
  return userFromClaims(claims);
}

// Upsert the user's profile row so uploads can show display name / avatar
// without re-parsing a token. Cheap; called on write actions.
export async function ensureUser(env, user) {
  await env.DB
    .prepare(
      `INSERT INTO users (id, discord_id, display_name, avatar_url, created_at)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         discord_id = excluded.discord_id,
         display_name = excluded.display_name,
         avatar_url = excluded.avatar_url`,
    )
    .bind(user.id, user.discord_id, user.display_name, user.avatar_url, Date.now())
    .run();
}
