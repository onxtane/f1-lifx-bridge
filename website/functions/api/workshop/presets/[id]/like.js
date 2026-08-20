// POST /api/workshop/presets/:id/like — toggle the caller's like.
// One like per (preset, token); the counter and join row move together in a
// batch (transaction) so they can't drift.

import { json, error, preflight, nowMs } from "../../_shared.js";
import { getUser } from "../../_auth.js";
import { enforce } from "../../_ratelimit.js";

export const onRequestOptions = () => preflight();

export async function onRequestPost({ request, params, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const user = await getUser(request, env);
  if (!user) return error("Sign in to like", 401);

  const limited = await enforce(env, [{ action: "like", id: user.id, limit: 60, windowSec: 60 }]);
  if (limited) return limited;

  const row = await env.DB.prepare(`SELECT status FROM presets WHERE id = ?`).bind(params.id).first();
  if (!row || row.status !== "public") return error("Preset not found", 404);

  const existing = await env.DB
    .prepare(`SELECT 1 AS x FROM likes WHERE preset_id = ? AND token = ?`)
    .bind(params.id, user.id)
    .first();

  let liked;
  if (existing) {
    // Toggle off: decrement/remove only while the preset is still public and the
    // like row exists — so an unlike can't touch a preset hidden mid-request.
    // Counter and row stay in sync (both no-op when hidden).
    await env.DB.batch([
      env.DB.prepare(
        `UPDATE presets SET likes = MAX(likes - 1, 0)
         WHERE id = ? AND status = 'public'
           AND EXISTS (SELECT 1 FROM likes WHERE preset_id = ? AND token = ?)`,
      ).bind(params.id, params.id, user.id),
      env.DB.prepare(
        `DELETE FROM likes
         WHERE preset_id = ? AND token = ?
           AND EXISTS (SELECT 1 FROM presets WHERE id = ? AND status = 'public')`,
      ).bind(params.id, user.id, params.id),
    ]);
    liked = false;
  } else {
    // Toggle on: increment only if the preset is still public and not already
    // liked, then record. The status guard + NOT EXISTS + conditional insert make
    // it race-safe (no PK violation, no double-increment, no like on a hidden preset).
    await env.DB.batch([
      env.DB.prepare(
        `UPDATE presets SET likes = likes + 1
         WHERE id = ? AND status = 'public'
           AND NOT EXISTS (SELECT 1 FROM likes WHERE preset_id = ? AND token = ?)`,
      ).bind(params.id, params.id, user.id),
      env.DB.prepare(
        `INSERT INTO likes (preset_id, token, created_at)
         SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM presets WHERE id = ? AND status = 'public')
         ON CONFLICT(preset_id, token) DO NOTHING`,
      ).bind(params.id, user.id, nowMs(), params.id),
    ]);
    liked = true;
  }

  const after = await env.DB.prepare(`SELECT likes, status FROM presets WHERE id = ?`).bind(params.id).first();
  if (!after || after.status !== "public") return error("Preset not found", 404);
  return json({ ok: true, liked, likes: after.likes });
}
