// Fixed-window rate limiting for the write endpoints, backed by D1 (no extra
// binding to provision). Each check increments a per-(action, identity, window)
// counter and rejects with 429 once the limit is passed. Windows are coarse
// buckets of `windowSec`, so a burst is capped per window rather than smoothed —
// simple and good enough to stop scripted abuse; precise smoothing would want a
// Durable Object.

import { json } from "./_shared.js";

async function bump(env, bucket, expiresAt) {
  // One round-trip: upsert-increment and read the new value via RETURNING.
  const row = await env.DB
    .prepare(
      `INSERT INTO rate_limits (bucket, count, expires_at) VALUES (?, 1, ?)
       ON CONFLICT(bucket) DO UPDATE SET count = count + 1
       RETURNING count`,
    )
    .bind(bucket, expiresAt)
    .first();
  return row?.count ?? 1;
}

async function hit(env, action, id, limit, windowSec) {
  const now = Date.now();
  const windowMs = windowSec * 1000;
  const windowIndex = Math.floor(now / windowMs);
  const bucket = `${action}:${id}:${windowIndex}`;
  const expiresAt = (windowIndex + 1) * windowMs;

  const count = await bump(env, bucket, expiresAt);

  // Opportunistic cleanup so the table stays bounded without a cron.
  if (Math.random() < 0.02) {
    try { await env.DB.prepare(`DELETE FROM rate_limits WHERE expires_at < ?`).bind(now).run(); } catch {}
  }

  return { ok: count <= limit, retryAfterSec: Math.max(1, Math.ceil((expiresAt - now) / 1000)) };
}

// Run a list of { action, id, limit, windowSec } checks. Returns a 429 Response
// as soon as one is exceeded, or null if all pass. Checks with a falsy id are
// skipped (e.g. an unknown IP shouldn't key everyone into one bucket).
export async function enforce(env, checks) {
  for (const c of checks) {
    if (!c.id) continue;
    const r = await hit(env, c.action, c.id, c.limit, c.windowSec);
    if (!r.ok) {
      return json(
        { error: "Rate limit exceeded — please slow down and try again shortly." },
        429,
        { "Retry-After": String(r.retryAfterSec) },
      );
    }
  }
  return null;
}

// Best-effort client IP. Cloudflare always sets CF-Connecting-IP in prod; local
// `wrangler pages dev` may not, so this can be null there (per-IP check skipped).
export function clientIp(request) {
  const ip = request.headers.get("CF-Connecting-IP") || request.headers.get("X-Forwarded-For");
  return ip ? ip.split(",")[0].trim() : null;
}
