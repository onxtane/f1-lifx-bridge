-- CodeRabbit follow-ups: personal-tab index coverage + a storage-level stars guard.

-- The personal tabs filter likes/downloads by `token` only. The composite PK
-- (preset_id, token) leads with preset_id, so a token-only lookup can't use it —
-- these subqueries would full-scan. Index the token column.
CREATE INDEX idx_likes_token     ON likes(token);
CREATE INDEX idx_downloads_token ON downloads(token);

-- Keep rating_sum/rating_count trustworthy at the storage layer: stars must be
-- 1–5 regardless of which endpoint writes them. SQLite can't ADD a CHECK to an
-- existing table, so rebuild ratings (a tiny table); only valid rows are carried.
CREATE TABLE ratings_new (
  preset_id  TEXT NOT NULL,
  token      TEXT NOT NULL,
  stars      INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
  created_at INTEGER NOT NULL,
  PRIMARY KEY (preset_id, token)
);
INSERT INTO ratings_new (preset_id, token, stars, created_at)
  SELECT preset_id, token, stars, created_at FROM ratings WHERE stars BETWEEN 1 AND 5;
DROP TABLE ratings;
ALTER TABLE ratings_new RENAME TO ratings;

-- Recompute the denormalized aggregates from the cleaned table so they can't
-- still reflect any discarded (out-of-range) rows.
UPDATE presets SET
  rating_sum   = COALESCE((SELECT SUM(stars) FROM ratings WHERE ratings.preset_id = presets.id), 0),
  rating_count = COALESCE((SELECT COUNT(*)   FROM ratings WHERE ratings.preset_id = presets.id), 0);
