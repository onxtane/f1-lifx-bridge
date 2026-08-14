// GET /api/workshop/presets — browse/list (Discover).
// Query: game, sort (hot|new|likes), q, cursor, limit.
// Personal filters (mine/liked/downloaded via X-GG-Token) land in a later pass;
// this is the public read path that unblocks front-end wiring.

import {
  json, error, preflight, encodeCursor, decodeCursor,
  swatchFromTheme, parseJsonArray, ratingOf,
} from "./_shared.js";
import { gameName } from "./_games.js";

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
  const presets = rows.slice(0, limit).map((r) => ({
    id: r.id,
    title: r.title,
    game: r.game,                       // slug → ui/logos/<slug>.png
    game_name: gameName(r.game),        // display name (registry)
    author_name: r.author_name,
    tags: parseJsonArray(r.tags),
    devices: parseJsonArray(r.devices),
    downloads: r.downloads,
    likes: r.likes,
    rating: ratingOf(r.rating_sum, r.rating_count),
    rating_count: r.rating_count,
    created_at: r.created_at,
    swatch: swatchFromTheme(r.theme_json),
  }));

  return json({ presets, cursor: hasMore ? encodeCursor(offset + limit) : null });
}
