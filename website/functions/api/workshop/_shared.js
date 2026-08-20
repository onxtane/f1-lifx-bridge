// Shared helpers for the Community Workshop API.
// Underscore-prefixed → Cloudflare Pages excludes this from routing but still
// lets sibling Functions import it. House style mirrors ../request-title.js
// (JSON envelope, { error } shape), plus CORS so the desktop app — a legitimate
// cross-origin consumer — can call these endpoints from its webview.

import { gameName } from "./_games.js";

export const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
  // Authorization is required for the write path (Bearer <supabase jwt>); it is
  // not a CORS-safelisted header, so it must be listed or the preflight blocks
  // every authenticated cross-origin request.
  "Access-Control-Allow-Headers": "Content-Type,Authorization",
  "Access-Control-Max-Age": "86400",
};

export function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...CORS, ...extraHeaders },
  });
}

export function error(message, status = 400) {
  return json({ error: message }, status);
}

// Preflight for browsers/webviews that send OPTIONS before a cross-origin call.
export function preflight() {
  return new Response(null, { status: 204, headers: CORS });
}

// Opaque, offset-based pagination cursor. Kept deliberately dumb; the shape is
// an implementation detail the client must not parse (matches the doc's §4.1).
export function encodeCursor(offset) {
  return btoa(JSON.stringify({ o: offset }));
}

export function decodeCursor(cursor) {
  if (!cursor) return 0;
  try {
    const { o } = JSON.parse(atob(cursor));
    return Number.isInteger(o) && o >= 0 ? o : 0;
  } catch {
    return 0;
  }
}

// The card swatch is the preset's rpm_gradient, echoed on list rows so the
// master column + LED arcs render from one fetch (docs §4.1). theme_json is a
// full envelope (§2.1); the gradient lives at .theme.rpm_gradient.
export function swatchFromTheme(themeJson) {
  try {
    const envelope = JSON.parse(themeJson);
    const stops = envelope?.theme?.rpm_gradient;
    return Array.isArray(stops) ? stops.slice(0, 6) : [];
  } catch {
    return [];
  }
}

// Shape a presets row into the list-card contract (design doc §4.1). One place
// so the list, upload response, and any future card producer stay identical.
// The row must include: id, title, game, author_name, tags, devices, downloads,
// likes, rating_sum, rating_count, created_at, theme_json.
export function toCard(r, officialIds) {
  return {
    id: r.id,
    title: r.title,
    game: r.game,                    // slug → ui/logos/<slug>.png
    game_name: gameName(r.game),     // display name (registry)
    author_name: r.author_name,
    // Official = owned by a GridGlow team account (env OFFICIAL_USER_IDS). Drives
    // the verified badge/check; based on the owner id, so author_name can't spoof it.
    official: isOfficial(r.owner_user_id, officialIds),
    tags: parseJsonArray(r.tags),
    devices: parseJsonArray(r.devices),
    downloads: r.downloads,
    likes: r.likes,
    rating: ratingOf(r.rating_sum, r.rating_count),
    rating_count: r.rating_count,
    created_at: r.created_at,
    swatch: swatchFromTheme(r.theme_json),
  };
}

// The set of GridGlow team user ids, from the OFFICIAL_USER_IDS env var
// (comma-separated Supabase user ids). Empty when unset — nothing is "official".
export function officialSet(env) {
  return new Set(
    String((env && env.OFFICIAL_USER_IDS) || "")
      .split(",").map((s) => s.trim()).filter(Boolean)
  );
}

export function isOfficial(ownerUserId, officialIds) {
  return !!(ownerUserId && officialIds && officialIds.has(ownerUserId));
}

// tags/devices are stored as JSON-array text; parse defensively for responses.
export function parseJsonArray(text) {
  try {
    const v = JSON.parse(text);
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

// Average star rating for a card, rounded to 1 dp; null when nobody has rated
// (so the front end can hide the stars rather than show a fake 0.0).
export function ratingOf(sum, count) {
  return count > 0 ? Math.round((sum / count) * 10) / 10 : null;
}

// Sortable-ish id: time prefix (base36 ms) + random suffix. Not a strict ULID,
// but monotonic-enough for created_at ties and collision-safe in practice.
export function newId() {
  const time = Date.now().toString(36).toUpperCase().padStart(9, "0");
  let rand = "";
  const bytes = crypto.getRandomValues(new Uint8Array(10));
  for (const b of bytes) rand += (b % 36).toString(36).toUpperCase();
  return `01${time}${rand}`.slice(0, 26);
}

// Parse a JSON request body, tolerating empty/invalid → null.
export async function readJson(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

export const nowMs = () => Date.now();
