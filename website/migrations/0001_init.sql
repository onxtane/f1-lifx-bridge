-- Community Workshop — initial schema.
-- See docs/community-workshop-backend.md §3.1. Metadata is relational; the
-- validated preset envelope (§2.1) is stored verbatim as JSON text in theme_json.

CREATE TABLE presets (
  id           TEXT PRIMARY KEY,            -- ULID/uuid, generated server-side
  title        TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  game         TEXT NOT NULL,               -- slug (ui/logos/<slug>.png) or "*"
  author_name  TEXT NOT NULL DEFAULT 'Anonymous',
  owner_token  TEXT NOT NULL,               -- hashed client token (§5); enables My Uploads
  theme_json   TEXT NOT NULL,               -- validated preset envelope (§2.1) as text
  schema_ver   INTEGER NOT NULL DEFAULT 1,
  tags         TEXT NOT NULL DEFAULT '[]',  -- JSON string[] — discovery tags (mockup data-tags)
  devices      TEXT NOT NULL DEFAULT '[]',  -- JSON string[] ⊆ [lifx,hue,nanoleaf] (mockup data-devices)
  downloads    INTEGER NOT NULL DEFAULT 0,
  likes        INTEGER NOT NULL DEFAULT 0,
  rating_sum   INTEGER NOT NULL DEFAULT 0,  -- Σ of 1–5 star ratings; avg = sum/count (mockup data-rating)
  rating_count INTEGER NOT NULL DEFAULT 0,
  status       TEXT NOT NULL DEFAULT 'public',  -- public | hidden | removed
  created_at   INTEGER NOT NULL,            -- epoch ms
  updated_at   INTEGER NOT NULL
);
CREATE INDEX idx_presets_game  ON presets(game, status);
CREATE INDEX idx_presets_hot   ON presets(status, downloads DESC);
CREATE INDEX idx_presets_owner ON presets(owner_token, status);

-- One like per (preset, token); reconstructs the "Liked" tab.
CREATE TABLE likes (
  preset_id  TEXT NOT NULL,
  token      TEXT NOT NULL,                 -- hashed client token
  created_at INTEGER NOT NULL,
  PRIMARY KEY (preset_id, token)
);

-- Dedupes the download counter; reconstructs the "My Downloads" tab.
CREATE TABLE downloads (
  preset_id  TEXT NOT NULL,
  token      TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (preset_id, token)
);

-- One rating per (preset, token); a re-rate overwrites. The presets.rating_sum
-- / rating_count aggregates are kept in step in the same transaction as writes
-- here (submission endpoint lands in a later pass — the table exists now so the
-- read path can expose an average without a schema change later).
CREATE TABLE ratings (
  preset_id  TEXT NOT NULL,
  token      TEXT NOT NULL,
  stars      INTEGER NOT NULL,             -- 1–5
  created_at INTEGER NOT NULL,
  PRIMARY KEY (preset_id, token)
);
