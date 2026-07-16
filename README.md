# GridGlow

Sync your LIFX, Nanoleaf, and Philips Hue lights to live sim racing events. Start lights sweep red zone by zone, yellow flags pulse amber, fastest laps go purple — every moment on track reflected in your room.

Supports **F1 25, F1 24, F1 2023, F1 2022, F1 2021**, **DiRT Rally 2.0**, **Forza Horizon 6, Forza Horizon 5, Forza Motorsport**, and **EA SPORTS WRC** via UDP telemetry. More titles coming.

---

## How it works

F1 25/24/23/22/21, DiRT Rally 2.0, the Forza titles, and EA SPORTS WRC broadcast telemetry over UDP on your local network. GridGlow listens for those packets, parses the event data, and sends the corresponding lighting effect to your LIFX, Nanoleaf, and Hue devices over LAN — no cloud, no API keys, sub-second latency.

![Data flow diagram](docs/data_flow.png)

---

## Features

**F1 Race Events**
- Start lights sequence — zones fill red one by one on multizone strips and Nanoleaf panels
- Lights out — green flash
- Yellow flag / Safety car — amber pulse
- Blue flag — blue pulse
- Red flag — urgent strobe
- Fastest lap — purple flash
- Chequered flag
- White warning / penalty
- Track clear / neutral return
- RPM meter — multizone strips fill with live engine revs and blink at the redline; the colour ramp is yours to set (Settings → Effects → Multizone), with presets or up to six custom stops

**DiRT Rally 2.0 Stage Events**
- Stage start — green flash when the stage begins
- Split checkpoint — purple flash at each split
- Stage finish — celebration flash at the end of the stage
- Crash — sharp white impact flash on collision (G-force + speed-drop detection)
- Service park — warm return to idle on exit from stage

**Forza Events** *(Horizon 6, Horizon 5, Motorsport)*
- Race start — green flash when the race begins
- Return to menus — warm return to idle
- Crash — sharp white impact flash on hard collisions. **Horizon 6 only** — Horizon 5 and Motorsport don't send the collision field this needs, so they get race start and return-to-menus.

**EA SPORTS WRC Stage Events**
- Stage start — green flash when the stage begins
- Split checkpoint — purple flash at each third of the stage
- Stage finish — celebration flash at the end of the stage
- Service park — warm return to idle

**Supported Lights**
- **LIFX** — bulbs, colour strips, multizone strips (zone-by-zone sweep)
- **Nanoleaf** — Canvas, Shapes, Lines, Elements, Light Panels (Panel Layout UI for position-based sweep)
- **Philips Hue** — full bulb support via local CLIP v2 API; Gradient Lightstrip Plus per-segment control (hardware validation pending [#44](https://github.com/onxtane/f1-lifx-bridge/issues/44))

**Light Control**
- Discover lights on your LAN
- Select which lights respond to which events (Light Assignment)
- Save and load light groups (Profiles)
- Master brightness range (min / max scaling)
- Stagger mode — fire each bulb with a configurable delay
- Identify button — flash a single bulb to confirm which one it is
- Idle mode — custom colour with optional slow pulse

**App**
- Game selector — switch between F1 25–21, DiRT Rally 2.0, Forza (Horizon 6 / 5 / Motorsport), and EA SPORTS WRC; "Remember my choice" skips the screen next time
- Mini mode — compact 380×100 always-on-top window
- Profiles — save and switch complete configurations
- UDP forwarding — relay packets to a second destination (sim dashboard, second PC)
- Built-in tutorial overlay
- Live packet and event log

---

## Supported Games

| Game | Status |
|------|--------|
| F1® 25 | ✅ Supported |
| F1® 24 | ✅ Supported |
| F1® 2021–2023 | ✅ Supported |
| Forza Horizon 6 | ✅ Supported |
| Forza Horizon 5 | ✅ Supported *(race events; no crash — [#52](https://github.com/onxtane/f1-lifx-bridge/issues/52))* |
| Forza Motorsport | ✅ Supported *(race events; no crash — [#54](https://github.com/onxtane/f1-lifx-bridge/issues/54))* |
| DiRT Rally 2.0 | ✅ Supported |
| EA SPORTS WRC | ✅ Supported |
| Assetto Corsa | 🔜 Planned — [#49](https://github.com/onxtane/f1-lifx-bridge/issues/49) |
| Project CARS 2 | 🔜 Planned — [#50](https://github.com/onxtane/f1-lifx-bridge/issues/50) |

See the full roadmap at [f1-lifx-bridge.pages.dev/roadmap](https://f1-lifx-bridge.pages.dev/roadmap) or [#31](https://github.com/onxtane/f1-lifx-bridge/issues/31).

---

## Requirements

- Windows 10/11 *(macOS support in development — [#45](https://github.com/onxtane/f1-lifx-bridge/issues/45))*
- F1 25/24/23/22/21, DiRT Rally 2.0, Forza (Horizon 6 / 5 / Motorsport), or EA SPORTS WRC on PC with UDP telemetry enabled
- LIFX, Nanoleaf, or Philips Hue device on the same LAN

GridGlow draws its window using the **Microsoft Edge WebView2 runtime** and **.NET Framework 4.6.2+**.
Both ship with Windows 10/11, so there's normally nothing to install — but they're absent from some
LTSC, stripped-down, and VM images. If either is missing, GridGlow says so on startup and points you
at Microsoft's installer rather than failing silently.

### Running from source

```
pip install pywebview lifxlan nanoleafapi requests psutil

# Windows — WebView2 backend (Edge runtime is preinstalled on Win 10/11):
pip install pythonnet
# macOS — native WKWebView backend (pyobjc is pulled in by pywebview automatically)

python main.py
```

### Running the tests

Replay-based integration tests feed crafted packet bytes through the parse/dispatch
pipeline — no hardware or network required:

```
python -m unittest discover -s tests
```

When running from source (or with `GRIDGLOW_DEV=1` set), a **Settings → Advanced → Run tests**
button runs the same suite from inside the app. The `tests/` folder is never bundled into
release builds.

### Replay tools

The replays send crafted UDP packets to a running bridge, so you can watch real effects on
real lights without launching a game. They're built into the app under **Settings → Advanced
→ Effect Replays** (turn on Advanced mode in Settings → App), and also available from a
terminal. Start GridGlow, pick an F1 title, start the bridge, then:

```
python tools/replay_f1_effects.py      # every F1 effect in order (--list, --effect NAME, --delay, --loop)
python tools/replay_sector_status.py   # live sector status across the three sectors
python tools/replay_rpm_meter.py       # the RPM / redline meter (--speed)
```

The CLI and the in-app buttons are two front-ends over the same `replay.py`, and its packets
come from `replay_packets.py` — which the dispatch tests also build with, so a replay can't
drift from the real packet format.

---

## Setup

**1. Enable UDP telemetry in your game**

**F1 25 / F1 24 / F1 2023 / F1 2022 / F1 2021** — Settings → Telemetry Settings

| Setting | Value |
|---|---|
| UDP Telemetry | On |
| UDP Broadcast Mode | Off |
| UDP IP Address | your PC's local IP (e.g. `192.168.1.x`) |
| UDP Port | `20777` (default) |
| UDP Send Rate | 60 Hz recommended |
| Your Telemetry | Public |

**DiRT Rally 2.0** — Options → Accessibility → UDP Telemetry

| Setting | Value |
|---|---|
| UDP Telemetry | Enabled |
| IP Address | `127.0.0.1` (or your PC's LAN IP) |
| Port | `20777` (default) |
| extradata | `3` |

**Forza Horizon 6 / Horizon 5 / Motorsport** — Settings → HUD and Gameplay → DATA OUT

| Setting | Value |
|---|---|
| Data Out | On |
| Data Out IP Address | `127.0.0.1` (or your PC's LAN IP) |
| Data Out Port | `5300` |

**EA SPORTS WRC** — WRC configures telemetry with a JSON file, so GridGlow installs it for you. Pick EA SPORTS WRC in the game selector, then go to **Settings → Connection → Install WRC telemetry config**. (Manual steps are in [`assets/wrc/README.md`](assets/wrc/README.md).)

**2. Run the app**

Launch the `.exe` from the release.

> ⚠️ **Not code-signed yet.** GridGlow isn't signed with a code-signing certificate, so Windows SmartScreen may show a *"Windows protected your PC"* warning (and some antivirus may flag the PyInstaller bundle). It's safe to run — click **More info → Run anyway**. Code signing is tracked in [#60](https://github.com/onxtane/f1-lifx-bridge/issues/60).

**3. Set up your lights**

On first launch the brand picker will guide you through pairing your lights. You can also reach it later via Settings.

**4. Select your game and start the bridge**

The game selector appears on launch. Pick your title, click **Launch**, then **Start Bridge** on the dashboard.

---

## Project structure

```
f1_lifx_app/
├── main.py                  # pywebview window + JS API layer
├── bridge_runner.py         # threading wrapper, settings dispatch, game switching
├── bridge_core.py           # F1 UDP listener, packet parsing, all lighting effects
├── runtime_check.py         # startup gate: WebView2 / .NET present, or explain why not
├── replay.py                # effect replays, shared by Settings -> Advanced and tools/
├── replay_packets.py        # byte-accurate F1 packet builders (replays + tests)
├── dr2_bridge.py            # DiRT Rally 2.0 UDP listener (extends bridge_core)
├── forza_bridge.py          # Forza Data Out UDP listener (extends bridge_core)
├── wrc_bridge.py            # EA SPORTS WRC UDP listener (extends bridge_core)
├── app_paths.py             # cross-platform resource / user-data paths
├── nanoleaf_controller.py   # Nanoleaf local REST API
├── hue_controller.py        # Philips Hue CLIP v2 local API
├── assets/wrc/              # WRC telemetry structure installed into the game
└── ui/
    └── index.html           # full single-file UI
```

---

## Configuration files

Created automatically on first run. Not tracked in git.

| File | Contents |
|---|---|
| `f1lifx_gui_settings.json` | Port, IP, brightness, stagger, idle colour, RPM gradient, enabled events, profiles |
| `lifx_groups.json` | Saved light groups |
| `nanoleaf_settings.json` | Nanoleaf IP, auth token, panel layout *(gitignored — contains credentials)* |
| `hue_settings.json` | Hue bridge IP, application key *(gitignored — contains credentials)* |
