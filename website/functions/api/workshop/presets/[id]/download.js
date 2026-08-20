// POST /api/workshop/presets/:id/download
// Requires a logged-in user. Idempotent per user: records the download (for the
// My Downloads tab) and bumps the counter once. Returns the preset's theme, so it
// doubles as the fetch the app applies via its set_* setters.

import { json, error, preflight, nowMs } from "../../_shared.js";
import { getUser } from "../../_auth.js";
import { enforce } from "../../_ratelimit.js";

export const onRequestOptions = () => preflight();

export async function onRequestPost({ request, params, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const user = await getUser(request, env);
  if (!user) return error("Sign in to download", 401);

  const limited = await enforce(env, [{ action: "download", id: user.id, limit: 60, windowSec: 60 }]);
  if (limited) return limited;

  const row = await env.DB
    .prepare(`SELECT theme_json, downloads, status FROM presets WHERE id = ?`)
    .bind(params.id)
    .first();
  if (!row || row.status !== "public") return error("Preset not found", 404);

  // Parse the theme before any writes so a corrupt row fails cleanly.
  let theme;
  try {
    theme = JSON.parse(row.theme_json).theme;
  } catch {
    return error("Preset data is corrupt", 502);
  }

  // Atomic: bump the counter only when this is a new (preset, user) download, then
  // record it — one batch (transaction) so the counter and join row can't drift.
  const results = await env.DB.batch([
    env.DB.prepare(
      `UPDATE presets SET downloads = downloads + 1
       WHERE id = ? AND NOT EXISTS (SELECT 1 FROM downloads WHERE preset_id = ? AND token = ?)`,
    ).bind(params.id, params.id, user.id),
    env.DB.prepare(
      `INSERT OR IGNORE INTO downloads (preset_id, token, created_at) VALUES (?, ?, ?)`,
    ).bind(params.id, user.id, nowMs()),
  ]);
  const incremented = (results[0]?.meta?.changes ?? 0) > 0;
  const downloads = row.downloads + (incremented ? 1 : 0);

  return json({ ok: true, downloads, theme });
}
