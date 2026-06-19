# F1 LIFX Bridge

Sync your LIFX smart lights to live F1 25 race events. Start lights, yellow flags, fastest laps, safety car — every moment on track reflected in your room.

---

## How it works

F1 25 broadcasts telemetry over UDP on your local network. This app listens for those packets, parses the event data, and sends the corresponding lighting effect to your LIFX bulbs and strips over LAN — no cloud, no API keys, sub-second latency.

```
F1 25 → UDP telemetry → F1 LIFX Bridge → LIFX LAN protocol → your lights
```

---

## Features

**Race Events**
- Start lights sequence (zones fill red one by one on multizone strips)
- Lights out (green flash)
- Yellow flag / Safety car
- Blue flag
- Red flag
- Fastest lap (purple)
- Chequered flag
- White warning / Black flag
- Track clear / Neutral return

**Lights**
- Discover all LIFX bulbs and strips on your LAN
- Select which lights respond to events
- Save and load light groups
- Stagger lights (fire each bulb with a configurable delay)
- Master brightness range (min / max scaling)
- Idle mode with custom color and optional slow pulse

**Multizone Strips**
- Zone sweep on start lights (fills zones left-to-right or right-to-left)
- Configurable direction per-strip
- Dedicated green-to-red zone fill test

**App**
- Profiles — save and switch complete configurations (lights, effects, settings)
- Light Assignment — assign specific lights to specific effects
- Intensity Curves — per-effect brightness curve editor (preview, backend coming)
- UDP forwarding — relay packets to a second destination (sim dashboard software, etc.)
- Built-in tutorial overlay
- Live packet and event log

---

## Requirements

- Python 3.10+
- F1 25 on PC with UDP telemetry enabled
- LIFX bulbs or strips on the same LAN

### Python dependencies

```
pip install pywebview PySide6 lifxlan
```

---

## Setup

**1. Enable F1 25 UDP telemetry**

In-game: Settings → Telemetry Settings

| Setting | Value |
|---|---|
| UDP Telemetry | On |
| UDP Broadcast Mode | Off |
| UDP IP Address | your PC's local IP (e.g. `192.168.1.x`) |
| UDP Port | `20777` (default) |
| UDP Send Rate | 60Hz recommended |
| Your Telemetry | Public |

**2. Run the app**

```bash
python main.py
```

**3. Discover your lights**

Go to the Lights page → click Discover Lights → select the bulbs you want to use.

**4. Start the bridge**

Click **Start Bridge** on the Dashboard. It opens the UDP listener and stays running in the background. You only need to do this once per session.

---

## Project structure

```
f1_lifx_app/
├── main.py            # pywebview window + JS API layer
├── bridge_runner.py   # threading wrapper, stat polling
├── bridge_core.py     # UDP listener, packet parsing, LIFX effects
└── ui/
    └── index.html     # full single-file UI
```

---

## Configuration files

These are created automatically on first run and are not tracked in git (user-specific):

| File | Contents |
|---|---|
| `f1lifx_gui_settings.json` | Port, IP, brightness, stagger, idle color, enabled events |
| `lifx_groups.json` | Saved light groups |

---

## Known issues

See the [GitHub Issues](https://github.com/onxtane/f1-lifx-bridge/issues) tracker for current bugs and in-progress work. Notable ones:

- Tailscale / VPN connections can break LIFX discovery ([#1](https://github.com/onxtane/f1-lifx-bridge/issues/1))
- Multizone strips occasionally detected as single bulbs ([#2](https://github.com/onxtane/f1-lifx-bridge/issues/2))
- App flickers when F1 25 comes into focus ([#3](https://github.com/onxtane/f1-lifx-bridge/issues/3))

---

## Roadmap

- Light Assignment backend wiring ([#6](https://github.com/onxtane/f1-lifx-bridge/issues/6))
- Profiles backend wiring ([#7](https://github.com/onxtane/f1-lifx-bridge/issues/7))
- Intensity Curves real-world implementation ([#5](https://github.com/onxtane/f1-lifx-bridge/issues/5))
- Formation Lap event ([#4](https://github.com/onxtane/f1-lifx-bridge/issues/4) maybe)
- Expanded multizone effects — live sector status, RPM meter ([#12](https://github.com/onxtane/f1-lifx-bridge/issues/12))
- Mini mode ([#11](https://github.com/onxtane/f1-lifx-bridge/issues/11))
