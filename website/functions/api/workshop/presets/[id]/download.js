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
    .prepare(`SELECT theme_json, status FROM presets WHERE id = ?`)
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

  // Atomic + visibility-guarded: both writes require a still-public preset (inside
  // the batch), so a preset hidden between the read above and now can't be
  // recorded against. Counter bumps only for a new (preset, user) download.
  await env.DB.batch([
    env.DB.prepare(
      `UPDATE presets SET downloads = downloads + 1
       WHERE id = ? AND status = 'public'
         AND NOT EXISTS (SELECT 1 FROM downloads WHERE preset_id = ? AND token = ?)`,
    ).bind(params.id, params.id, user.id),
    env.DB.prepare(
      `INSERT INTO downloads (preset_id, token, created_at)
       SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM presets WHERE id = ? AND status = 'public')
       ON CONFLICT(preset_id, token) DO NOTHING`,
    ).bind(params.id, user.id, nowMs(), params.id),
  ]);

  // Re-read: reflects concurrent downloads, and 404s the race where it was hidden.
  const after = await env.DB.prepare(`SELECT downloads, status FROM presets WHERE id = ?`).bind(params.id).first();
  if (!after || after.status !== "public") return error("Preset not found", 404);
  return json({ ok: true, downloads: after.downloads, theme });
}
