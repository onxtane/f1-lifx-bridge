// GET /api/workshop/presets/:id — full preset (detail panel + live-preview source).
// Returns the whole validated envelope (§2.1) so the front end can drive every
// preview colour from `theme` and download applies it via the app's set_* setters.

import { json, error, preflight, parseJsonArray, ratingOf, nowMs } from "../_shared.js";
import { gameName } from "../_games.js";
import { getUser } from "../_auth.js";
import { enforce } from "../_ratelimit.js";

export const onRequestOptions = () => preflight();

// DELETE /api/workshop/presets/:id — soft-remove, owner only (signed-in user match).
export async function onRequestDelete({ request, params, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const user = await getUser(request, env);
  if (!user) return error("Sign in", 401);

  const limited = await enforce(env, [{ action: "delete", id: user.id, limit: 30, windowSec: 60 }]);
  if (limited) return limited;

  const row = await env.DB
    .prepare(`SELECT owner_user_id, status FROM presets WHERE id = ?`)
    .bind(params.id)
    .first();
  if (!row || row.status === "removed") return error("Preset not found", 404);
  if (row.owner_user_id !== user.id) return error("Not your preset", 403);

  await env.DB
    .prepare(`UPDATE presets SET status = 'removed', updated_at = ? WHERE id = ?`)
    .bind(nowMs(), params.id)
    .run();
  return json({ ok: true });
}

export async function onRequestGet({ params, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  let row;
  try {
    row = await env.DB
      .prepare(
        `SELECT id, title, description, game, author_name, tags, devices,
                downloads, likes, rating_sum, rating_count,
                created_at, updated_at, theme_json
         FROM presets
         WHERE id = ? AND status = 'public'`
      )
      .bind(params.id)
      .first();
  } catch (e) {
    console.error("workshop detail query failed", e);
    return error("Could not load preset", 502);
  }

  if (!row) return error("Preset not found", 404);

  let envelope;
  try {
    envelope = JSON.parse(row.theme_json);
  } catch {
    return error("Preset data is corrupt", 502);
  }

  return json({
    id: row.id,
    title: row.title,
    description: row.description,
    game: row.game,                       // slug → ui/logos/<slug>.png
    game_name: gameName(row.game),        // display name (registry)
    author_name: row.author_name,
    tags: parseJsonArray(row.tags),
    devices: parseJsonArray(row.devices),
    downloads: row.downloads,
    likes: row.likes,
    rating: ratingOf(row.rating_sum, row.rating_count),
    rating_count: row.rating_count,
    created_at: row.created_at,
    updated_at: row.updated_at,
    gridglow_preset: envelope.gridglow_preset,
    app_min_version: envelope.app_min_version,
    theme: envelope.theme,
  });
}
