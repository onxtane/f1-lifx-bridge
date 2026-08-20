// Upload validation for the Community Workshop (design doc §4.3).
// Kept as a standalone module (no Function/Request deps) so the desktop app can
// reuse the same rules for an upload pre-flight. validatePreset() returns either
// { ok: true, value } with a normalized row-ready object, or
// { ok: false, status, error } for a 4xx.

import { isKnownGame } from "./_games.js";

const THEME_MAX_BYTES = 16 * 1024;          // §4.3.1 size cap on the theme envelope
const HEX = /^#[0-9a-fA-F]{6}$/;
const IP_LIKE = /\b\d{1,3}(\.\d{1,3}){3}\b/;

// Effect keys the app understands (bridge_core.py / ui/index.html). enabled_events
// must be a subset; unknown keys are rejected so a preset can't smuggle behaviour.
const KNOWN_EVENTS = new Set([
  "start_lights", "fastest_lap", "sector_status", "rpm_meter",
  "red_flag", "yellow_flag", "blue_flag", "black_flag", "chequered_flag",
]);

const KNOWN_DEVICES = new Set(["lifx", "hue", "nanoleaf"]);

// Effects that carry custom colours (Effect Customization). per_light isn't
// portable (keyed by the sharer's own light labels), so it's rejected here —
// only the Sync-All colours and per-zone stops travel.
const KNOWN_EFFECT_COLOR_KEYS = new Set([
  "start_lights", "lights_out", "yellow_flag", "blue_flag", "red_flag",
  "fastest_lap", "chequered_flag", "white_warning", "crash",
]);
const EFFECT_COLOR_SLOTS = new Set(["main", "a", "b"]);

// §4.3.3 portability guard — a shared preset must be device-agnostic. Reject any
// theme carrying device/network identity, whatever the nesting.
const FORBIDDEN_KEYS = new Set([
  "labels", "label", "groups", "group", "host", "port", "ip", "ips",
  "token", "tokens", "auth", "mac", "serial", "bridge_ip", "address",
]);

const fail = (status, error) => ({ ok: false, status, error });

// Trim + strip anything angle-bracketed; collapse whitespace; cap length.
function cleanStr(v, max) {
  return String(v == null ? "" : v)
    .replace(/<[^>]*>/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
}

function isPlainObject(v) {
  return v != null && typeof v === "object" && !Array.isArray(v);
}

// True if any branch nests deeper than `max`. Bails early at max+1, so it can
// never itself overflow — used to reject pathological themes before the
// (recursive) device-identity walk runs.
function exceedsDepth(node, max, depth = 0) {
  if (depth > max) return true;
  if (Array.isArray(node)) return node.some((v) => exceedsDepth(v, max, depth + 1));
  if (isPlainObject(node)) return Object.values(node).some((v) => exceedsDepth(v, max, depth + 1));
  return false;
}

// Walk the theme looking for device-identifying keys or IP-shaped strings.
function hasDeviceIdentity(node) {
  if (typeof node === "string") return IP_LIKE.test(node);
  if (Array.isArray(node)) return node.some(hasDeviceIdentity);
  if (isPlainObject(node)) {
    for (const [k, v] of Object.entries(node)) {
      if (FORBIDDEN_KEYS.has(k.toLowerCase())) return true;
      if (hasDeviceIdentity(v)) return true;
    }
  }
  return false;
}

function validateTheme(theme) {
  if (!isPlainObject(theme)) return "theme must be an object";

  const {
    enabled_events, brightness_range, stagger,
    idle_state, mz_startlights, rpm_gradient, curves, effect_colors,
  } = theme;

  if (enabled_events != null) {
    if (!Array.isArray(enabled_events)) return "enabled_events must be an array or null";
    for (const e of enabled_events) {
      if (!KNOWN_EVENTS.has(e)) return `unknown event key: ${e}`;
    }
  }

  if (brightness_range != null) {
    const { min_pct, max_pct } = brightness_range || {};
    if (!inRange(min_pct, 0, 100) || !inRange(max_pct, 0, 100)) return "brightness_range min_pct/max_pct must be 0–100";
    if (min_pct > max_pct) return "brightness_range min_pct must be ≤ max_pct";
  }

  if (stagger != null) {
    if (typeof stagger.enabled !== "boolean" || !Number.isInteger(stagger.ms) || stagger.ms < 0) return "stagger must be { enabled: bool, ms: int≥0 }";
  }

  if (idle_state != null) {
    if (!HEX.test(idle_state.color_hex || "") || typeof idle_state.pulse !== "boolean") return "idle_state must be { color_hex: #RRGGBB, pulse: bool }";
  }

  if (mz_startlights != null) {
    if (!["ltr", "rtl"].includes(mz_startlights.direction) || !["sweep", "solid"].includes(mz_startlights.mode)) return "mz_startlights direction must be ltr|rtl and mode sweep|solid";
  }

  if (rpm_gradient != null) {
    if (!Array.isArray(rpm_gradient) || rpm_gradient.length < 1 || rpm_gradient.length > 12) return "rpm_gradient must be 1–12 hex stops";
    for (const c of rpm_gradient) if (!HEX.test(c)) return `rpm_gradient stop not #RRGGBB: ${c}`;
  }

  if (curves != null) {
    if (!isPlainObject(curves)) return "curves must be an object";
    for (const [name, c] of Object.entries(curves)) {
      if (!Array.isArray(c?.points) || !Number.isInteger(c?.duration_ms) || c.duration_ms < 0) return `curve '${name}' must be { points: [...], duration_ms: int≥0 }`;
      for (const p of c.points) {
        if (!Array.isArray(p) || p.length !== 2 || typeof p[0] !== "number" || typeof p[1] !== "number") return `curve '${name}' points must be [t, v] number pairs`;
      }
    }
  }

  if (effect_colors != null) {
    if (!isPlainObject(effect_colors)) return "effect_colors must be an object";
    const keys = Object.keys(effect_colors);
    if (keys.length > 12) return "effect_colors has too many effects";
    for (const [k, e] of Object.entries(effect_colors)) {
      if (!KNOWN_EFFECT_COLOR_KEYS.has(k)) return `unknown effect_colors key: ${k}`;
      if (!isPlainObject(e)) return `effect_colors '${k}' must be an object`;
      if (e.mode != null && !["all", "per_zone"].includes(e.mode)) return `effect_colors '${k}' mode must be all|per_zone`;
      if ("per_light" in e) return `effect_colors '${k}' per_light is not shareable (device-specific)`;
      if (e.colors != null) {
        if (!isPlainObject(e.colors)) return `effect_colors '${k}'.colors must be an object`;
        for (const [slot, hex] of Object.entries(e.colors)) {
          if (!EFFECT_COLOR_SLOTS.has(slot)) return `effect_colors '${k}' unknown slot: ${slot}`;
          if (!HEX.test(hex || "")) return `effect_colors '${k}'.${slot} not #RRGGBB`;
        }
      }
      if (e.per_zone != null) {
        if (!Array.isArray(e.per_zone) || e.per_zone.length < 1 || e.per_zone.length > 96) return `effect_colors '${k}'.per_zone must be 1–96 hex stops`;
        for (const c of e.per_zone) if (!HEX.test(c)) return `effect_colors '${k}'.per_zone stop not #RRGGBB: ${c}`;
      }
      if (e.colors == null && e.per_zone == null) return `effect_colors '${k}' needs colours or per_zone`;
    }
  }

  if (exceedsDepth(theme, 16)) return "theme is nested too deeply";
  if (hasDeviceIdentity(theme)) return "theme contains device-identifying data (labels/host/ip/token) — presets must be device-agnostic";
  return null; // ok
}

function inRange(n, lo, hi) {
  return typeof n === "number" && Number.isFinite(n) && n >= lo && n <= hi;
}

// body: the parsed upload JSON (envelope §2.1 + card metadata). Returns
// { ok, value } where value is ready for the presets INSERT.
export function validatePreset(body) {
  if (!isPlainObject(body)) return fail(400, "Invalid request body");

  if (body.gridglow_preset !== 1) return fail(422, "unsupported gridglow_preset version");

  const title = cleanStr(body.title, 80);
  if (!title) return fail(400, "title is required");

  const game = cleanStr(body.game, 40) || "*";
  if (!isKnownGame(game)) return fail(422, `unknown game slug: ${game}`);

  // Size cap FIRST — before the recursive theme walk — so an oversized or deeply
  // nested payload is rejected cheaply instead of driving the walk to a blow-up.
  const themeText = isPlainObject(body.theme) ? JSON.stringify(body.theme) : "";
  if (themeText.length > THEME_MAX_BYTES) return fail(413, "theme too large");

  const themeErr = validateTheme(body.theme);
  if (themeErr) return fail(422, themeErr);

  // tags: up to 10, short, lowercased, unique.
  let tags = Array.isArray(body.tags) ? body.tags : [];
  tags = [...new Set(tags.map((t) => cleanStr(t, 24).toLowerCase()).filter(Boolean))].slice(0, 10);

  // devices: subset of the three supported controllers.
  let devices = Array.isArray(body.devices) ? body.devices : [];
  devices = [...new Set(devices.map((d) => cleanStr(d, 12).toLowerCase()))].filter((d) => KNOWN_DEVICES.has(d));

  const value = {
    title,
    description: cleanStr(body.description, 500),
    game,
    author_name: cleanStr(body.author_name, 40) || "Anonymous",
    app_min_version: cleanStr(body.app_min_version, 20) || "0.10.0",
    tags: JSON.stringify(tags),
    devices: JSON.stringify(devices),
    // Store the full validated envelope verbatim (what the read path returns).
    theme_json: JSON.stringify({
      gridglow_preset: 1,
      app_min_version: cleanStr(body.app_min_version, 20) || "0.10.0",
      game,
      theme: body.theme,
    }),
  };
  return { ok: true, value };
}
