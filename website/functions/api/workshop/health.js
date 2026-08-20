// GET /api/workshop/health — liveness + D1 check for a status monitor.
// 200 { ok:true, service, db:"up", presets, time } when healthy; 503 when the
// D1 binding is missing or a query fails. No auth, not rate-limited.
//
// Also detects the "Functions got dropped from a deploy" failure mode: if the
// workshop Functions aren't in the deployment, this path serves the marketing
// HTML instead of JSON — so a monitor that asserts on `"ok": true` (not just a
// 200 status) will flag it.

import { json, preflight } from "./_shared.js";

export const onRequestOptions = () => preflight();

export async function onRequestGet({ env }) {
  if (!env.DB) return json({ ok: false, service: "workshop", db: "unbound" }, 503);
  try {
    const row = await env.DB
      .prepare(`SELECT count(*) AS n FROM presets WHERE status = 'public'`)
      .first();
    return json({
      ok: true,
      service: "workshop",
      db: "up",
      presets: row?.n ?? 0,
      time: new Date().toISOString(),
    });
  } catch (e) {
    console.error("workshop health db check failed", e);
    return json({ ok: false, service: "workshop", db: "down" }, 503);
  }
}
