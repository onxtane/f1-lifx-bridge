// Shared helpers for the Community Workshop API.
// Underscore-prefixed → Cloudflare Pages excludes this from routing but still
// lets sibling Functions import it. House style mirrors ../request-title.js
// (JSON envelope, { error } shape), plus CORS so the desktop app — a legitimate
// cross-origin consumer — can call these endpoints from its webview.

export const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type,X-GG-Token",
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

// Salted SHA-256 of a client token (§5). Not used by the read path yet — lives
// here so the write/personal-tab passes share one hashing definition. The salt
// comes from an env var so it never ships in source.
export async function hashToken(token, salt) {
  const data = new TextEncoder().encode(`${salt || ""}:${token}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
