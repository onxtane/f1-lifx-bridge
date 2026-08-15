// /api/workshop/presets
//   GET  — browse/list (Discover) + personal tabs (mine|liked|downloaded).
//   POST — upload a preset.

import {
  json, error, preflight, encodeCursor, decodeCursor,
  toCard, ownerHash, newId, readJson, nowMs,
} from "./_shared.js";
import { validatePreset } from "./_validate.js";

const DEFAULT_LIMIT = 24;
const MAX_LIMIT = 50;

// Allowlist: the value is interpolated into SQL, so it must never come from the
// user directly — only these fixed clauses are reachable.
const SORTS = {
  hot: "downloads DESC",
  new: "created_at DESC",
  likes: "likes DESC",
  rating: "CAST(rating_sum AS REAL) / MAX(rating_count, 1) DESC",
};

export const onRequestOptions = () => preflight();

export async function onRequestGet({ request, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const url = new URL(request.url);
  const game = (url.searchParams.get("game") || "").trim();
  const q = (url.searchParams.get("q") || "").trim();
  const orderBy = SORTS[(url.searchParams.get("sort") || "hot").toLowerCase()] || SORTS.hot;

  let limit = parseInt(url.searchParams.get("limit"), 10);
  if (!Number.isInteger(limit) || limit <= 0) limit = DEFAULT_LIMIT;
  limit = Math.min(limit, MAX_LIMIT);
  const offset = decodeCursor(url.searchParams.get("cursor"));

  const where = ["status = 'public'"];
  const binds = [];
  if (game && game !== "*") {
    where.push("game = ?");
    binds.push(game);
  }
  if (q) {
    // tags is JSON text; a LIKE over it matches on tag substrings too.
    where.push("(title LIKE ? OR description LIKE ? OR tags LIKE ?)");
    binds.push(`%${q}%`, `%${q}%`, `%${q}%`);
  }

  // Personal tabs (§5): scoped to the caller's hashed token. Any of these with
  // no X-GG-Token yields an empty list rather than leaking everyone's rows.
  const mine = url.searchParams.get("mine") === "1";
  const liked = url.searchParams.get("liked") === "1";
  const downloaded = url.searchParams.get("downloaded") === "1";
  if (mine || liked || downloaded) {
    const token = await ownerHash(request, env);
    if (!token) return json({ presets: [], cursor: null });
    if (mine) { where.push("owner_token = ?"); binds.push(token); }
    if (liked) { where.push("id IN (SELECT preset_id FROM likes WHERE token = ?)"); binds.push(token); }
    if (downloaded) { where.push("id IN (SELECT preset_id FROM downloads WHERE token = ?)"); binds.push(token); }
  }

  // Secondary sort by id keeps ordering stable when the primary key ties.
  // Fetch limit+1 to detect a next page without a second COUNT query.
  const sql =
    `SELECT id, title, game, author_name, tags, devices,
            downloads, likes, rating_sum, rating_count, created_at, theme_json
     FROM presets
     WHERE ${where.join(" AND ")}
     ORDER BY ${orderBy}, id DESC
     LIMIT ? OFFSET ?`;
  binds.push(limit + 1, offset);

  let rows;
  try {
    const res = await env.DB.prepare(sql).bind(...binds).all();
    rows = res.results || [];
  } catch (e) {
    console.error("workshop list query failed", e);
    return error("Could not list presets", 502);
  }

  const hasMore = rows.length > limit;
  const presets = rows.slice(0, limit).map(toCard);
  return json({ presets, cursor: hasMore ? encodeCursor(offset + limit) : null });
}

export async function onRequestPost({ request, env }) {
  if (!env.DB) return error("Server misconfiguration — workshop storage unavailable", 500);

  const body = await readJson(request);
  if (!body) return error("Invalid request body", 400);

  // Honeypot — bots fill the hidden field, humans don't (mirrors request-title.js).
  if (body._trap) return json({ ok: true });

  const owner = await ownerHash(request, env);
  if (!owner) return error("Missing X-GG-Token — a client token is required to upload", 401);

  const result = validatePreset(body);
  if (!result.ok) return error(result.error, result.status);
  const v = result.value;

  const id = newId();
  const ts = nowMs();
  try {
    await env.DB
      .prepare(
        `INSERT INTO presets
           (id, title, description, game, author_name, owner_token, theme_json,
            tags, devices, downloads, likes, rating_sum, rating_count,
            status, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 'public', ?, ?)`
      )
      .bind(id, v.title, v.description, v.game, v.author_name, owner, v.theme_json,
            v.tags, v.devices, ts, ts)
      .run();
  } catch (e) {
    console.error("workshop upload insert failed", e);
    return error("Could not save preset", 502);
  }

  // Return the created row as a list card so the client can insert it optimistically.
  const row = await env.DB
    .prepare(
      `SELECT id, title, game, author_name, tags, devices,
              downloads, likes, rating_sum, rating_count, created_at, theme_json
       FROM presets WHERE id = ?`
    )
    .bind(id)
    .first();

  return json({ ok: true, id, preset: row ? toCard(row) : { id } }, 201);
}
