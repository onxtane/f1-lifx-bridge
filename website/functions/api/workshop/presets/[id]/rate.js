// POST /api/workshop/presets/:id/rate — submit or change a 1–5 star rating.
// Body: { stars: 1..5 }. One rating per (preset, token); re-rating overwrites and
// adjusts the aggregate by the delta so the average stays correct.

import { json, error, preflight, readJson, ratingOf, nowMs } from "../../_shared.js";
import { getUser } from "../../_auth.js";
import { enforce } from "../../_ratelimit.js";

export const onRequestOptions = () => preflight();

export async function onRequestPost({ request, params, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const user = await getUser(request, env);
  if (!user) return error("Sign in to rate", 401);

  const limited = await enforce(env, [{ action: "rate", id: user.id, limit: 30, windowSec: 60 }]);
  if (limited) return limited;

  const body = await readJson(request);
  const stars = Number(body?.stars);
  if (!Number.isInteger(stars) || stars < 1 || stars > 5) return error("stars must be an integer 1–5", 400);

  const row = await env.DB.prepare(`SELECT status FROM presets WHERE id = ?`).bind(params.id).first();
  if (!row || row.status !== "public") return error("Preset not found", 404);

  const prev = await env.DB
    .prepare(`SELECT stars FROM ratings WHERE preset_id = ? AND token = ?`)
    .bind(params.id, user.id)
    .first();
  const ts = nowMs();

  if (prev) {
    const delta = stars - prev.stars;
    await env.DB.batch([
      env.DB.prepare(`UPDATE ratings SET stars = ?, created_at = ? WHERE preset_id = ? AND token = ?`).bind(stars, ts, params.id, user.id),
      env.DB.prepare(`UPDATE presets SET rating_sum = rating_sum + ? WHERE id = ?`).bind(delta, params.id),
    ]);
  } else {
    // Race-safe first rating: only bump the aggregate if WE created the row.
    const ins = await env.DB
      .prepare(`INSERT OR IGNORE INTO ratings (preset_id, token, stars, created_at) VALUES (?, ?, ?, ?)`)
      .bind(params.id, user.id, stars, ts)
      .run();
    if ((ins.meta?.changes ?? 0) > 0) {
      await env.DB
        .prepare(`UPDATE presets SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 WHERE id = ?`)
        .bind(stars, params.id).run();
    } else {
      // Lost the insert race — a concurrent request created the row. Treat as an
      // update: adjust rating_sum by the delta from the now-stored value.
      const cur = await env.DB.prepare(`SELECT stars FROM ratings WHERE preset_id = ? AND token = ?`).bind(params.id, user.id).first();
      const delta = stars - (cur?.stars ?? stars);
      await env.DB.batch([
        env.DB.prepare(`UPDATE ratings SET stars = ?, created_at = ? WHERE preset_id = ? AND token = ?`).bind(stars, ts, params.id, user.id),
        env.DB.prepare(`UPDATE presets SET rating_sum = rating_sum + ? WHERE id = ?`).bind(delta, params.id),
      ]);
    }
  }

  const after = await env.DB
    .prepare(`SELECT rating_sum, rating_count FROM presets WHERE id = ?`)
    .bind(params.id)
    .first();
  // The preset may have been removed between the write and this read.
  if (!after) return json({ ok: true, rating: null, rating_count: 0, your_rating: stars });
  return json({
    ok: true,
    rating: ratingOf(after.rating_sum, after.rating_count),
    rating_count: after.rating_count,
    your_rating: stars,
  });
}
