-- Dev-only seed data for the Community Workshop read path.
-- NOT a migration — apply by hand against the LOCAL D1 only:
--   npx wrangler d1 execute gridglow-workshop --local --file=./seed/dev-seed.sql
-- Gives the list/detail endpoints real rows (varied games, downloads, likes) so
-- the front end has something to render against before the upload path exists.
-- Each theme_json is a full preset envelope (docs/community-workshop-backend.md §2.1).

INSERT INTO presets
  (id, title, description, game, author_name, owner_token, theme_json, tags, devices,
   downloads, likes, rating_sum, rating_count, created_at, updated_at)
VALUES
  ('01J8ZK9Q2MONACO0NIGHT0F125', 'Monaco Night', 'Deep navy idle with a punchy green→red rev ramp for street circuits.',
   'f1_25', 'vettel_fan', 'seed-owner-hash-a',
   '{"gridglow_preset":1,"app_min_version":"0.10.0","game":"f1_25","theme":{"enabled_events":["start_lights","fastest_lap","sector_status","rpm_meter"],"brightness_range":{"min_pct":10,"max_pct":100},"stagger":{"enabled":true,"ms":40},"idle_state":{"color_hex":"#101820","pulse":false},"mz_startlights":{"direction":"ltr","mode":"sweep"},"rpm_gradient":["#00ff88","#ffd400","#ff2d2d"],"curves":{"shift":{"points":[[0,0],[0.2,1],[1,0]],"duration_ms":260}}}}',
   '["immersive","race","dynamic","reactive","flags"]', '["hue","nanoleaf","lifx"]',
   412, 88, 552, 115, 1723507200000, 1723507200000),

  ('01J8ZK9Q2SUZUKA0RAIN00F125', 'Suzuka Rain', 'Cooler cyan-biased ramp and a soft pulsing idle for wet sessions.',
   'f1_25', 'wetline', 'seed-owner-hash-b',
   '{"gridglow_preset":1,"app_min_version":"0.10.0","game":"f1_25","theme":{"enabled_events":["start_lights","fastest_lap","sector_status","rpm_meter","yellow_flag","blue_flag"],"brightness_range":{"min_pct":15,"max_pct":90},"stagger":{"enabled":true,"ms":60},"idle_state":{"color_hex":"#0a2a3a","pulse":true},"mz_startlights":{"direction":"rtl","mode":"solid"},"rpm_gradient":["#22d3ee","#a3e635","#f97316"],"curves":{"shift":{"points":[[0,0],[0.25,1],[1,0]],"duration_ms":300}}}}',
   '["dark","moody","night","endurance","brake"]', '["lifx"]',
   137, 41, 189, 42, 1723680000000, 1723680000000),

  ('01J8ZK9Q2MONTE0CARLO0WRC01', 'Rally Sunset', 'Warm amber sweep tuned for stage starts and pace-note flags.',
   'wrc', 'gravel_god', 'seed-owner-hash-c',
   '{"gridglow_preset":1,"app_min_version":"0.10.0","game":"wrc","theme":{"enabled_events":["start_lights","sector_status","rpm_meter"],"brightness_range":{"min_pct":20,"max_pct":100},"stagger":{"enabled":false,"ms":0},"idle_state":{"color_hex":"#2a1405","pulse":false},"mz_startlights":{"direction":"ltr","mode":"sweep"},"rpm_gradient":["#fbbf24","#fb7185","#7c3aed"],"curves":{"shift":{"points":[[0,0],[0.3,1],[1,0]],"duration_ms":240}}}}',
   '["rally","gravel","tarmac","intense","pace"]', '["nanoleaf","lifx"]',
   58, 12, 59, 12, 1723766400000, 1723766400000);
