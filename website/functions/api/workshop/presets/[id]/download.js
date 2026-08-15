// POST /api/workshop/presets/:id/download
// Idempotent per token: records the download (for the My Downloads tab) and bumps
// the counter once. Returns the preset's theme, so it doubles as the fetch the
// app applies via its set_* setters. Anonymous (no token) downloads still work —
// they just aren't counted or recorded.

import { json, error, preflight, ownerHash, nowMs } from "../../_shared.js";

export const onRequestOptions = () => preflight();

export async function onRequestPost({ request, params, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const row = await env.DB
    .prepare(`SELECT theme_json, downloads, status FROM presets WHERE id = ?`)
    .bind(params.id)
    .first();
  if (!row || row.status !== "public") return error("Preset not found", 404);

  let downloads = row.downloads;
  const token = await ownerHash(request, env);
  if (token) {
    const ins = await env.DB
      .prepare(`INSERT OR IGNORE INTO downloads (preset_id, token, created_at) VALUES (?, ?, ?)`)
      .bind(params.id, token, nowMs())
      .run();
    if ((ins.meta?.changes ?? 0) > 0) {
      await env.DB.prepare(`UPDATE presets SET downloads = downloads + 1 WHERE id = ?`).bind(params.id).run();
      downloads += 1;
    }
  }

  let theme = null;
  try {
    theme = JSON.parse(row.theme_json).theme;
  } catch {
    return error("Preset data is corrupt", 502);
  }

  return json({ ok: true, downloads, theme });
}
