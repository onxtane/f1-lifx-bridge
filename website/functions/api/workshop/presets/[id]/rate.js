// POST /api/workshop/presets/:id/rate — submit or change a 1–5 star rating.
// Body: { stars: 1..5 }. One rating per (preset, token); re-rating overwrites,
// and the preset's rating_sum/rating_count are recomputed from the ratings table
// so the average stays correct under concurrency.

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

  const ts = nowMs();
  // Upsert the caller's rating (guarded on a still-public preset, so a preset
  // hidden between the read above and now can't be rated), then recompute the
  // aggregate from the ratings table. Deriving rating_sum/rating_count from source
  // (not delta math) keeps them correct under concurrent re-rates. One batch.
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO ratings (preset_id, token, stars, created_at)
       SELECT ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM presets WHERE id = ? AND status = 'public')
       ON CONFLICT(preset_id, token) DO UPDATE SET stars = excluded.stars, created_at = excluded.created_at`,
    ).bind(params.id, user.id, stars, ts, params.id),
    env.DB.prepare(
      `UPDATE presets SET
         rating_sum = (SELECT COALESCE(SUM(stars), 0) FROM ratings WHERE preset_id = ?),
         rating_count = (SELECT COUNT(*) FROM ratings WHERE preset_id = ?)
       WHERE id = ? AND status = 'public'`,
    ).bind(params.id, params.id, params.id),
  ]);

  const after = await env.DB
    .prepare(`SELECT rating_sum, rating_count, status FROM presets WHERE id = ?`)
    .bind(params.id)
    .first();
  // 404 the race where the preset was hidden/removed during the write.
  if (!after || after.status !== "public") return error("Preset not found", 404);
  return json({
    ok: true,
    rating: ratingOf(after.rating_sum, after.rating_count),
    rating_count: after.rating_count,
    your_rating: stars,
  });
}
