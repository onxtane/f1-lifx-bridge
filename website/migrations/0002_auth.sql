-- Real accounts (design doc §5, revised): Supabase-issued logins. Provider-neutral
-- — the chosen method is Supabase email/password, but the schema and verifier don't
-- care (email/password, Google, and Discord all mint the same ES256 JWT).
-- Additive over 0001 (which is already applied to the remote/prod DB).
--
-- Identity model change: the like/download/rating join tables' `token` column now
-- stores the authenticated Supabase user id (claims.sub) instead of a hashed
-- self-issued device token — same "one per (preset, user)" meaning, no schema
-- change needed there. presets gains a real owner_user_id; owner_token is kept
-- for the claim-migration of any pre-auth rows.

CREATE TABLE users (
  id           TEXT PRIMARY KEY,            -- Supabase user uuid (JWT sub)
  email        TEXT,                        -- present for email/password + most OAuth
  provider_id  TEXT,                        -- external provider id (OAuth); NULL for email/pw
  display_name TEXT NOT NULL DEFAULT 'Racer',
  avatar_url   TEXT,
  created_at   INTEGER NOT NULL             -- epoch ms
);

ALTER TABLE presets ADD COLUMN owner_user_id TEXT;   -- NULL for legacy/seed rows
CREATE INDEX idx_presets_owner_user ON presets(owner_user_id, status);
