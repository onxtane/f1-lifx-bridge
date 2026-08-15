// POST /api/workshop/presets/:id/rate — submit or change a 1–5 star rating.
// Body: { stars: 1..5 }. One rating per (preset, token); re-rating overwrites and
// adjusts the aggregate by the delta so the average stays correct.

import { json, error, preflight, readJson, ratingOf, nowMs } from "../../_shared.js";
import { getUser } from "../../_auth.js";

export const onRequestOptions = () => preflight();

export async function onRequestPost({ request, params, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const user = await getUser(request, env);
  if (!user) return error("Sign in with Discord to rate", 401);

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
    await env.DB.batch([
      env.DB.prepare(`INSERT INTO ratings (preset_id, token, stars, created_at) VALUES (?, ?, ?, ?)`).bind(params.id, user.id, stars, ts),
      env.DB.prepare(`UPDATE presets SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 WHERE id = ?`).bind(stars, params.id),
    ]);
  }

  const after = await env.DB
    .prepare(`SELECT rating_sum, rating_count FROM presets WHERE id = ?`)
    .bind(params.id)
    .first();
  return json({
    ok: true,
    rating: ratingOf(after.rating_sum, after.rating_count),
    rating_count: after.rating_count,
    your_rating: stars,
  });
}
