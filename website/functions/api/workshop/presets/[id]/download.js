// POST /api/workshop/presets/:id/download
// Requires a logged-in user. Idempotent per user: records the download (for the
// My Downloads tab) and bumps the counter once. Returns the preset's theme, so it
// doubles as the fetch the app applies via its set_* setters.

import { json, error, preflight, nowMs } from "../../_shared.js";
import { getUser } from "../../_auth.js";

export const onRequestOptions = () => preflight();

export async function onRequestPost({ request, params, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const user = await getUser(request, env);
  if (!user) return error("Sign in with Discord to download", 401);

  const row = await env.DB
    .prepare(`SELECT theme_json, downloads, status FROM presets WHERE id = ?`)
    .bind(params.id)
    .first();
  if (!row || row.status !== "public") return error("Preset not found", 404);

  let downloads = row.downloads;
  const ins = await env.DB
    .prepare(`INSERT OR IGNORE INTO downloads (preset_id, token, created_at) VALUES (?, ?, ?)`)
    .bind(params.id, user.id, nowMs())
    .run();
  if ((ins.meta?.changes ?? 0) > 0) {
    await env.DB.prepare(`UPDATE presets SET downloads = downloads + 1 WHERE id = ?`).bind(params.id).run();
    downloads += 1;
  }

  let theme = null;
  try {
    theme = JSON.parse(row.theme_json).theme;
  } catch {
    return error("Preset data is corrupt", 502);
  }

  return json({ ok: true, downloads, theme });
}
