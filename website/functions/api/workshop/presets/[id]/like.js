// POST /api/workshop/presets/:id/like — toggle the caller's like.
// One like per (preset, token); the counter and join row move together in a
// batch (transaction) so they can't drift.

import { json, error, preflight, nowMs } from "../../_shared.js";
import { getUser } from "../../_auth.js";

export const onRequestOptions = () => preflight();

export async function onRequestPost({ request, params, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const user = await getUser(request, env);
  if (!user) return error("Sign in with Discord to like", 401);

  const row = await env.DB.prepare(`SELECT status FROM presets WHERE id = ?`).bind(params.id).first();
  if (!row || row.status !== "public") return error("Preset not found", 404);

  const existing = await env.DB
    .prepare(`SELECT 1 AS x FROM likes WHERE preset_id = ? AND token = ?`)
    .bind(params.id, user.id)
    .first();

  let liked;
  if (existing) {
    await env.DB.batch([
      env.DB.prepare(`DELETE FROM likes WHERE preset_id = ? AND token = ?`).bind(params.id, user.id),
      env.DB.prepare(`UPDATE presets SET likes = MAX(likes - 1, 0) WHERE id = ?`).bind(params.id),
    ]);
    liked = false;
  } else {
    await env.DB.batch([
      env.DB.prepare(`INSERT INTO likes (preset_id, token, created_at) VALUES (?, ?, ?)`).bind(params.id, user.id, nowMs()),
      env.DB.prepare(`UPDATE presets SET likes = likes + 1 WHERE id = ?`).bind(params.id),
    ]);
    liked = true;
  }

  const after = await env.DB.prepare(`SELECT likes FROM presets WHERE id = ?`).bind(params.id).first();
  return json({ ok: true, liked, likes: after?.likes ?? 0 });
}
