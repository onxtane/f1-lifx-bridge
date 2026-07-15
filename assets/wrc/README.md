# EA SPORTS WRC telemetry setup

EA WRC does not have a fixed telemetry packet like DiRT Rally 2.0. Instead it
emits whatever channels you define, in the order you define them, described by a
JSON "packet structure" file. GridGlow ships its own structure (`gridglow.json`)
so the byte layout the app parses is fixed and known.

## Install

1. Copy `gridglow.json` into:

   ```
   %USERPROFILE%\Documents\My Games\WRC\telemetry\udp\
   ```

2. Open `config.json` in `...\My Games\WRC\telemetry\` and add (or enable) a
   packet entry that points GridGlow's structure at the port GridGlow listens on
   (default `20777`):

   ```json
   {
       "structure": "gridglow",
       "packet": "session_update",
       "ip": "127.0.0.1",
       "port": 20777,
       "frequencyHz": 60,
       "bEnabled": true
   }
   ```

   Use your PC's LAN IP instead of `127.0.0.1` if GridGlow runs on a different
   machine.

3. Restart EA WRC so it reloads the telemetry config, then start a stage.

## What GridGlow reads

The `session_update` packet is little-endian. GridGlow only uses a handful of
the channels; the rest are carried for parity with the community layout so the
byte offsets stay stable:

| Channel | Offset | Type | Used for |
|---|---|---|---|
| `packet_4cc` | 0 | 4-byte tag (`SESU`) | Identifies the session-update packet |
| `vehicle_speed` | 45 | float32 | (reserved) |
| `vehicle_engine_rpm_current` | 61 | float32 | (reserved, redline effect) |
| `stage_current_time` | 85 | float32 | Stage running / stage start |
| `stage_result_status` | 101 | uint8 | Stage finish (0 = running, 1 = finished) |
| `stage_progress` | 102 | float32 | Split checkpoints (thirds of the stage) |

Layout confirmed against the community EA WRC parser
(github.com/arttusalminen/WRC-Telemetry), data schema version 3.
