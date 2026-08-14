# Community Workshop — Backend Design

**Status:** Design draft (no code yet) · **Date:** 2026-08-13
**Decisions locked for this pass:** Cloudflare D1 storage · auth deferred (anonymous browse/download/upload)

The Community Workshop lets GridGlow users **view, share, and download community lighting
presets**. The front-end concept mockup already exists
(`docs/mockups/workshop-community.html`) with Discover / My Uploads / My Downloads / Liked
tabs, a master-list + detail-panel split, and a live LED-zone preview. This document defines
the backend that will feed it: the shareable **preset schema**, the **API contract**, the
**D1 storage layer**, and the **deferred-auth** model — so that wiring the mockup to real data
is a swap, not a rewrite.

---

## 1. Goals & non-goals

**Goals**
- A public, read-mostly gallery of lighting presets, filterable by game.
- Anonymous upload, download, and "like" — no account required to start.
- Counters (downloads, likes) for sorting/discovery.
- Schema and endpoints stable enough that the front end can wire against them now and the
  storage/auth internals can change later without breaking the contract.

**Non-goals (this pass)**
- Real user accounts / OAuth (deferred — see §5).
- Preset *editing* server-side, comments, collections, or following.
- Serving game cover-art / logos (front-end asset concern; deferred per the mockup notes).
- Anything that runs inside the desktop app's Python process. This backend is web-only
  (Cloudflare Pages Functions); the app talks to it over HTTPS.

---

## 2. What a shareable preset *is*

A Workshop preset is **the effect theme**, not the user's device bindings. The desktop app
persists two very different things:

| App concept | Source | Shareable? |
|---|---|---|
| **Light groups** (`groups.json`) | `save_group(name, labels)` — named lists of *device labels* | **No** — bound to one user's physical lights |
| **Effect theme** (`gui_settings.json`) | `save_gui_settings(...)` — colours, gradients, curves, enabled events | **Yes** — device-agnostic |
| Network forwarding, Nanoleaf/Hue auth & layout | per-device setters | **No** — device/network-specific |

So a preset payload is a **projection of `gui_settings`** that keeps only the portable,
device-agnostic fields. The shapes below are taken from the current setters in
`bridge_runner.py`:

| Field | Setter of record | Shape |
|---|---|---|
| `enabled_events` | `set_enabled_events` | `string[]` of event keys, or `null` = all enabled |
| `brightness_range` | `set_brightness_range` | `{ min_pct: 0–100, max_pct: 0–100 }` |
| `stagger` | `set_stagger` | `{ enabled: bool, ms: int }` |
| `idle_state` | `set_idle_state` | `{ color_hex: "#RRGGBB", pulse: bool }` |
| `mz_startlights` | `set_mz_startlights` | `{ direction: "ltr"\|"rtl", mode: "sweep"\|"solid" }` |
| `rpm_gradient` | `set_rpm_gradient` | `string[]` of hex stops, e.g. `["#00ff00","#ffcc00","#ff0000"]` |
| `curves` | `set_curves` | `{ [label]: { points: [[t,v],…], duration_ms: int } }` |

**Known event keys** (from `bridge_core.py` / `ui/index.html`): `start_lights`, `fastest_lap`,
`sector_status`, `rpm_meter`, `red_flag`, `yellow_flag`, `blue_flag`, `black_flag`,
`chequered_flag`.

### 2.1 Preset document (the JSON that gets shared)

```jsonc
{
  "gridglow_preset": 1,          // envelope schema version (bump on breaking changes)
  "app_min_version": "0.10.0",   // oldest app that understands this theme; for forward-compat
  "game": "f1_25",               // slug matching ui/logos/<slug>.png; "*" for game-agnostic
  "theme": {                     // <-- the device-agnostic gui_settings projection (§2)
    "enabled_events": ["start_lights", "fastest_lap", "sector_status", "rpm_meter"],
    "brightness_range": { "min_pct": 10, "max_pct": 100 },
    "stagger": { "enabled": true, "ms": 40 },
    "idle_state": { "color_hex": "#101820", "pulse": false },
    "mz_startlights": { "direction": "ltr", "mode": "sweep" },
    "rpm_gradient": ["#00ff88", "#ffd400", "#ff2d2d"],
    "curves": { "shift": { "points": [[0,0],[0.2,1],[1,0]], "duration_ms": 260 } }
  }
}
```

- **`theme` is the contract with the live preview.** Every colour the mockup's LED-zone
  animation paints (rev ramp, start lights, shift flash, fastest-lap purple, flags) must be
  driven from `theme`, never hard-coded — this is the ⚠ DEV NOTE requirement already flagged
  in the mockup. The server treats `theme` as an **opaque, validated blob**: it stores and
  returns it verbatim and does not interpret individual effects.
- **Portability guard:** the upload endpoint rejects any theme that carries device-identifying
  fields (light labels, IPs, Nanoleaf/Hue tokens). See §4.3.
- **Versioning:** `gridglow_preset` is the envelope version; `app_min_version` lets an older
  app skip a preset it can't fully render. Mirrors the `versions.schema` / `versions.data`
  split already used in `assets/wrc/gridglow.json`.

---

## 3. Storage — Cloudflare D1

D1 (SQLite) fits the access patterns: list/filter by game, order by downloads/likes/recency,
atomic counter bumps. Metadata lives in relational tables; the theme blob is stored as JSON
text on the row (presets are small — a few KB — so R2 is unnecessary. If they grow, `theme`
can move to R2 behind the same API without a contract change).

### 3.1 Schema (`migrations/0001_init.sql`)

```sql
CREATE TABLE presets (
  id           TEXT PRIMARY KEY,            -- ULID/uuid, generated server-side
  title        TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  game         TEXT NOT NULL,               -- slug or "*"
  author_name  TEXT NOT NULL DEFAULT 'Anonymous',
  owner_token  TEXT NOT NULL,               -- hashed client token (see §5); enables My Uploads
  theme_json   TEXT NOT NULL,               -- validated preset envelope (§2.1) as text
  schema_ver   INTEGER NOT NULL DEFAULT 1,
  tags         TEXT NOT NULL DEFAULT '[]',  -- JSON string[] — discovery tags (mockup data-tags)
  devices      TEXT NOT NULL DEFAULT '[]',  -- JSON string[] ⊆ [lifx,hue,nanoleaf] (mockup data-devices)
  downloads    INTEGER NOT NULL DEFAULT 0,
  likes        INTEGER NOT NULL DEFAULT 0,
  rating_sum   INTEGER NOT NULL DEFAULT 0,  -- Σ 1–5 stars; avg = sum/count (mockup data-rating)
  rating_count INTEGER NOT NULL DEFAULT 0,
  status       TEXT NOT NULL DEFAULT 'public',  -- public | hidden | removed
  created_at   INTEGER NOT NULL,            -- epoch ms
  updated_at   INTEGER NOT NULL
);
CREATE INDEX idx_presets_game    ON presets(game, status);
CREATE INDEX idx_presets_hot     ON presets(status, downloads DESC);
CREATE INDEX idx_presets_owner   ON presets(owner_token, status);

-- tags/devices/rating were added so every field the mockup's cards show
-- (docs/mockups/workshop-community.html, data-* attrs) has a home in the API.
-- The `game` column stays a SLUG (ui/logos/<slug>.png); the API echoes the
-- display name via the registry in functions/api/workshop/_games.js, which
-- mirrors GS_CATALOG in ui/index.html. A `ratings` table (one row per
-- preset+token, mirroring likes/downloads) also exists for the later
-- rate-submission endpoint; the read path exposes the average now.

-- One like per (preset, token); also lets "Liked" and "My Downloads" tabs be reconstructed.
CREATE TABLE likes (
  preset_id  TEXT NOT NULL,
  token      TEXT NOT NULL,                 -- hashed client token
  created_at INTEGER NOT NULL,
  PRIMARY KEY (preset_id, token)
);

CREATE TABLE downloads (
  preset_id  TEXT NOT NULL,
  token      TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (preset_id, token)           -- dedupes counter; supports "My Downloads"
);
```

`likes.likes`/`downloads` counters on `presets` are denormalised for fast sorting and bumped
in the same transaction as the join-row insert.

---

## 4. API contract — Cloudflare Pages Functions

Lives under `website/functions/api/workshop/`, following the existing
`website/functions/api/request-title.js` house style (env-configured, JSON in/out, honeypot,
`X-GitHub`-style headers where relevant). All responses are `application/json`. The client
sends its token (see §5) in an `X-GG-Token` header on every request; reads work without it.

| Method & path | Purpose |
|---|---|
| `GET /api/workshop/presets` | List/browse (Discover). Query: `game`, `sort` (`hot`\|`new`\|`likes`), `q`, `cursor`, `limit`. Also powers My Uploads (`mine=1`) / Liked (`liked=1`) / My Downloads (`downloaded=1`) via the caller's token. |
| `GET /api/workshop/presets/:id` | Full preset incl. `theme` — the detail panel + download source. |
| `POST /api/workshop/presets` | Upload. Body = preset envelope (§2.1) + `title`, `description`, `author_name`, honeypot `_trap`. Validated (§4.3). |
| `POST /api/workshop/presets/:id/download` | Idempotent per token; bumps `downloads`, records row for "My Downloads". Returns the theme (so it doubles as the fetch). |
| `POST /api/workshop/presets/:id/like` | Toggle like; bumps/decrements `likes`. |
| `DELETE /api/workshop/presets/:id` | Soft-remove; allowed only if `X-GG-Token` hash matches `owner_token`. |

### 4.1 List response

```jsonc
{
  "presets": [
    { "id": "01J…", "title": "Monaco Night",
      "game": "f1_25",                 // slug → ui/logos/<slug>.png
      "game_name": "F1 25 – F1 21",    // display name (registry, _games.js)
      "author_name": "vettel_fan",
      "tags": ["immersive", "race", "dynamic", "reactive", "flags"],
      "devices": ["hue", "nanoleaf", "lifx"],
      "downloads": 412, "likes": 88,
      "rating": 4.8, "rating_count": 115,   // rating is null when nobody has rated
      "created_at": 1723500000000,
      "swatch": ["#00ff88", "#ffd400", "#ff2d2d"] }   // rpm_gradient echo for card LED strip
  ],
  "cursor": "eyJvIjoxMjB9"   // opaque; null when no more
}
```

The `swatch` lets the card list and LED-arc previews render without a second fetch — one list
call drives the whole master column, matching the "one data source of truth" front-end
principle. The detail panel and live preview then use `GET …/:id` for the full `theme`.

### 4.1a Card fields ↔ mockup `data-*`

Every attribute the mockup's `.pc` cards carry now maps to a response field, so wiring the
cards (in the settings-ui session) is: read from the list response instead of the static DOM.

| Mockup `data-*` | API field | Notes |
|---|---|---|
| `data-name` | `title` | |
| `data-game="F1 24"` | `game_name` | display; `game` slug drives the logo |
| `data-downloads` | `downloads` | `sort=hot` orders by this |
| `data-likes` | `likes` | `sort=likes` |
| `data-rating="4.8"` | `rating` (+ `rating_count`) | `sort=rating`; `null` = unrated |
| `data-days="20"` | `created_at` | client computes "N days ago" |
| `data-devices="hue,nanoleaf,lifx"` | `devices` | array; badge per entry |
| `data-tags="immersive,race,…"` | `tags` | array; also matched by `q` search |

`sort` accepts `hot` \| `new` \| `likes` \| `rating`. `q` searches title + description + tags.

### 4.2 Errors

`{ "error": "<message>" }` with `400` (bad body), `404`, `409` (duplicate), `413` (too large),
`422` (schema/portability rejection), `429` (rate limit), `502` (upstream). Same envelope as
`request-title.js`.

### 4.3 Upload validation (server-side, non-negotiable)

1. **Size cap** — reject `theme_json` over ~16 KB (`413`).
2. **Schema** — `gridglow_preset` known; every field matches §2 shapes; hex strings match
   `/^#[0-9a-fA-F]{6}$/`; `enabled_events` ⊆ known keys; numeric ranges clamped.
3. **Portability guard** — reject if the theme contains device-identifying keys (`labels`,
   `groups`, `host`, `port`, IP-shaped strings, Nanoleaf/Hue auth tokens) → `422`.
4. **Anti-spam** — honeypot `_trap` (silent OK, like `request-title.js`); per-IP + per-token
   rate limit; strip HTML from `title`/`description`/`author_name`.
5. **Moderation hook** — new rows default `status='public'`; a `report` path and an
   admin-token `hidden`/`removed` toggle come in a later pass (see §7).

---

## 5. Identity — deferred auth (client token)

No accounts this pass. On first run the client (app and/or website) generates a random
**opaque token** (UUIDv4) and stores it locally. It is sent as `X-GG-Token` and the server
stores only a **salted hash** of it. This is enough to power the three personal tabs:

- **My Uploads** — `presets.owner_token = hash(token)`
- **Liked** — join on `likes.token = hash(token)`
- **My Downloads** — join on `downloads.token = hash(token)`

Properties & limits (call these out to users later): the token is a *bearer* handle, not an
identity — anyone with it inherits the tabs; losing it loses the personal lists (uploads stay
public). When real auth lands (GitHub OAuth is the natural fit given the existing GitHub
integration in `request-title.js`), we migrate by letting a signed-in user **claim** rows whose
`owner_token` matches their current device token. The token column stays; OAuth is layered on
top, so **no contract break**.

---

## 6. Front-end wiring seam

Maps the mockup's tabs/panels to endpoints so integration is mechanical:

| Mockup element | Call |
|---|---|
| Discover tab (master list) | `GET /presets?game=&sort=hot` → cards from `swatch` |
| Game filter chips | `GET /presets?game=<slug>` |
| Detail panel + live LED preview | `GET /presets/:id` → drive preview from `theme` (**never hard-code colours**) |
| Download button | `POST /presets/:id/download` → apply theme via app's `save_gui_settings` |
| Like button | `POST /presets/:id/like` |
| My Uploads / Liked / My Downloads | `GET /presets?mine=1` / `?liked=1` / `?downloaded=1` |
| Share/upload | `POST /presets` with the §2.1 envelope |

The mockup should keep its single `PRESETS` array as the seam: replace the placeholder array
with a `fetch('/api/workshop/presets')` and point each render function (card list, LED arcs,
detail pane, live preview) at the response — a one-line swap, per the front-end-wiring memory.

**Applying a downloaded preset in the app:** the theme projection is exactly the subset
`save_gui_settings` already accepts, so `apply(theme)` is a loop of the existing setters
(`set_enabled_events`, `set_rpm_gradient`, `set_idle_state`, …). No new app persistence format
is needed — a downloaded preset is just a partial `gui_settings` payload.

---

## 7. Open questions / later passes

1. **Moderation** — report endpoint, admin token, `hidden`/`removed` workflow. Stubbed via the
   `status` column now.
2. **Real auth** — GitHub OAuth + row-claim migration (§5).
3. **Game slug registry** — presets reference `game` slugs (`ui/logos/<slug>.png`); a
   canonical list should be shared between app, website, and this validator.
4. **Abuse at scale** — Cloudflare WAF + Turnstile on upload if honeypot proves insufficient.
5. **Preset updates/versioning** — edit-in-place vs new-row-per-version (leaning new row;
   `id` immutable, so downloads/likes stay attributable).
6. **Featured / curated** — a `featured` flag or separate table for editorial rows.

---

## 8. Suggested build order (when we start coding)

1. `migrations/0001_init.sql` + D1 binding in `website/` (`wrangler.toml`).
2. Shared validator module (§4.3) — reused by upload and by an app-side pre-flight.
3. Read path: `GET /presets`, `GET /presets/:id` (unblocks front-end wiring immediately).
4. Write path: `POST /presets` with full validation.
5. Counters: `download` / `like` endpoints + join tables.
6. Personal tabs via `X-GG-Token`.
7. Moderation + auth in a later milestone.
