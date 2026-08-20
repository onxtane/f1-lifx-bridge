-- Fixed-window rate limiting for write endpoints (see _ratelimit.js).
-- Additive; safe on top of 0001 (prod) and 0002.

CREATE TABLE rate_limits (
  bucket     TEXT PRIMARY KEY,     -- "<action>:<identity>:<windowIndex>"
  count      INTEGER NOT NULL DEFAULT 0,
  expires_at INTEGER NOT NULL      -- epoch ms; dead rows cleaned opportunistically
);
CREATE INDEX idx_rate_limits_expires ON rate_limits(expires_at);
