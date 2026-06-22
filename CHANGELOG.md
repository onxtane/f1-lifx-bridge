# Changelog

All notable changes are documented here.

---

## [Unreleased]

### Added
- **Live sector status (F1)** — opt-in Auto-Response effect that maps the Session packet's marshal-zone flags onto the three track sectors and paints them across a multizone strip (green = clear, amber = yellow, blue = blue), splitting the strip to fit any zone count (8 zones → 2/3/3). Multizone-strip only: while active the strip is reserved for the sector display and other lights keep their normal flag flash; a red flag temporarily overrides the strip (whole strip red) then resumes sectors. Default off, F1-only. Test it without the game via `tools/replay_sector_status.py` (#12)

---

## [0.7.1] — 2026-06-22

### Added
- **Replay-based dispatch tests** — a `tests/` suite (stdlib `unittest`, no new dependencies, not bundled into the build) that feeds crafted UDP packet bytes through the real parse/dispatch pipeline and asserts the correct effect fires, with no LIFX/Nanoleaf/Hue hardware. Covers all F1 events and the five DiRT Rally 2.0 stage events. Runnable via `python -m unittest discover -s tests` or a hidden Settings → Developer → "Run tests" button (shown only in dev mode: running from source, or `GRIDGLOW_DEV=1`) (#36)

### Fixed
- **Nanoleaf auto-discovery** — replaced the single-socket SSDP probe (unreliable on multi-homed Windows hosts with VPN / Hyper-V / WSL virtual adapters) with a dependency-free scan that sends both mDNS (`_nanoleafapi._tcp.local.`) and SSDP probes from every LAN interface, using each reply's source address as the device IP and resolving the friendly device name from the mDNS instance label. Falls back to the previous library discovery only if the scan finds nothing (#23)
- **LIFX discovery over Tailscale / VPN** — the previous `source_ip=` mitigation was dead code (the bundled lifxlan constructor ignores it), so discovery still bound to `INADDR_ANY` and let the OS broadcast out a tunnel/virtual adapter — finding no bulbs, or on Windows aborting outright with `WinError 10054`. Discovery now binds its UDP socket to each real LAN interface (via a `LifxLAN` subclass), tries physical NICs first, skips the Tailscale `100.64.0.0/10` range, and keeps the interface that finds the most bulbs (#1)

---

## [0.7.0] — 2026-06-22

### Added
- **F1 2021–2023 support** — version-aware header parsing dispatches on packet format: F1 24/25 use the 29-byte header (with `m_gameYear`), F1 23 uses 28 bytes (no `m_gameYear`), F1 21/22 use 24 bytes (no `m_gameYear`, no `m_overallFrameIdentifier`). All offset-dependent functions (marshal zones, event code, start lights, FIA flags, fastest lap, penalty, retirement) now receive `header_size` dynamically. Game selector card updated to "F1® 25 · 24 · 23 · 22 · 21" (#46)
- **DiRT Rally 2.0 support** — new `dr2_bridge.py` listens on the 264-byte Codemasters telemetry format (`extradata=3`). Fires effects on stage start (green), split checkpoint (purple), stage finish (celebration), and return to service park (neutral). All UI sections (Manual Triggers, Auto-Response, Quick Effects, Light Assignment, Light Preview) are now game-aware and switch with the selected title; packet counter reflects the active game (#48)
- **Crash flash** — DiRT Rally 2.0 collision detection via combined G-force spike + single-packet speed drop (3 s cooldown); fires a sharp white impact flash (#48)
- **Intensity curves** — per-effect brightness curves (piecewise-linear, configurable duration) now drive both LIFX and Nanoleaf output during effect playback
- **Nanoleaf per-effect light assignment** — Nanoleaf devices honour the same Light Assignment semantics as LIFX, firing only for their assigned effects

### Fixed
- **Nanoleaf NL29 Canvas panel layout** — exclude controller/Rhythm modules by `shapeType` (1, 12, and `shapeType 3` on Canvas) so the first panel no longer renders as a stray hexagon (#22)
- **Concurrent settings corruption** — `save_gui_settings` now serialises read-modify-write under a lock, fixing JSON "Extra data" parse errors when switching games

### Changed
- **Website** — race-event simulation section is now a per-game carousel (F1 25–21 and DiRT Rally 2.0) with left/right navigation; EA WRC split into its own roadmap issue (#56)

---

## [0.6.0] — 2026-06-21

### Added
- **F1 24 support** — identical 29-byte UDP header to F1 25; both formats now accepted by the packet filter (`bridge_core.py`). Game selector card updated to "F1® 25 / 24"
- **Multi-title game selector** — Forza Horizon 5 / Motorsport, DiRT Rally 2.0 / EA WRC, Assetto Corsa, Project CARS 2, and F1® Manager shown as a coming-soon horizontal scroll strip below the active card

---

## [0.5.0] — 2026-06-21

### Added
- **Philips Hue integration** — full CLIP v2 local API client: bridge discovery (mDNS + cloud fallback), button-press pairing, CIE 1931 XY colour space conversion, brightness scaling (#42)
- **Gradient Lightstrip support** — per-segment start lights sweep on Hue Gradient Lightstrip Plus; up to 7 gradient points; unlit segments use idle colour (#43)
- **Brand picker** — Hue added to setup flow alongside LIFX; routes to bridge pairing screen on first run (#42)
- **Hue Settings section** — in-app configuration: bridge IP, paired status, light selection, enabled toggle (#42)
- **Game selector screen** — on launch, choose your title; routes through after any setup flow; "Remember my choice" skips the screen next time (#38)
- **Sidebar game selector button** — quick-access above the mini mode button (#38)
- **Skip game selector setting** — toggle in Settings → App to auto-launch with the last selected game (#38)

---

## [0.4.0] — 2026-06-20

### Added
- **Landing website** — Astro-based marketing page with animated race event cards, hover effect simulations, app screenshots, platform cards (LIFX, Nanoleaf, Philips Hue coming soon)
- **Philips Hue teaser** — coming-soon card on website and in-app; CLIP v2 integration tracked in #26

### Fixed
- Log view restyled — entries now show colour-coded badges (EVENT, GROUP, GUI, ERROR, WARNING, NANOLEAF, LAN) instead of raw terminal text (#29)
- Auto-response icons replaced with matching SVGs from Manual Triggers section — emojis removed (#27)
- Light list checkbox scroll glitch — replaced native checkboxes with CSS-driven custom ones; GPU scroll compositing can no longer corrupt visual state (#28)
- Velocity scrolling — rewrote scroll handler with `preventDefault()` to eliminate double-scroll (native + RAF); normalised `deltaMode` for consistent trackpad/mouse behaviour; added velocity cap
- Stagger Lights disabled with notice pending investigation (#30)

---

## [0.3.0] — 2026-06-20

### Added
- **Mini mode** — compact 380×100 always-on-top window with Start/Stop and status pill (#11)
- **Console Players** — LAN IP picker in UDP Connection settings; lists all active network interfaces so you can point F1 25 at the right adapter without guessing (#25)
- **Toast notifications** — slide-up banner confirms when a profile is saved (#15)
- **SVG icons** in Manual Triggers — replaced all emoji placeholders with inline SVGs (Tabler / Lucide / Phosphor); no CDN dependency (#10)

### Fixed
- Console Players: VPN detection now uses adapter name as primary signal; `10.x.x.x` Ethernet adapters no longer incorrectly flagged as VPN (#25)
- Console Players: monospace font stack now matches the rest of the UI (`"SF Mono", Consolas, monospace` instead of bare `monospace`) (#25)
- UDP listen address: default changed from `127.0.0.1` to `0.0.0.0` so the bridge receives packets without manual configuration (#17)
- UDP listen address: pending address change now applied when restarting a stopped bridge (#17)
- UDP listen IP input: validates IPv4 format before saving (#21)
- Multizone strip detection: improved reliability for devices that sometimes report as a single bulb (#2)
- Light assignments: deferred push until after discovery and saved groups are loaded (#18)
- Nanoleaf: deferred startup API calls until `pywebviewready` fires, eliminating spurious preview errors (#24)
- Light list: added right-side padding to prevent content from sitting under the scrollbar (#4)

---

## [0.2.0] — 2026-06-20

### Added
- **Nanoleaf integration** — full support for Canvas, Shapes, Lines, Elements, and Light Panels
  - All nine F1 race effects fire on Nanoleaf in sync with LIFX
  - One-time pairing via local REST API (no cloud required)
  - Discover button auto-fills device IP via SSDP
  - Nanoleaf device shown as always-active entry in Light Assignment
- **Panel Layout UI** — visualise and rearrange physical panel positions
  - Correct shape detection per device type (square, hexagon, triangle, etc.)
  - Drag panels to match real-world arrangement
- **Start lights sweep for Nanoleaf** — panels light up by physical position (bottom→top or top→bottom), matching LIFX multizone behaviour
- **Test Multizone** button wired to Nanoleaf panels
- **EXE build** — PyInstaller spec (`f1_lifx_bridge.spec`); distributable folder at `dist/F1LifxBridge/`

### Fixed
- LIFX and Nanoleaf effects now fire in parallel (previously sequential, causing visible lag)
- Nanoleaf fade and timing: switched to static effect with `transitionTime=0` for instant flashes
- Nanoleaf silent failure: HTTP status now checked with fallback to `/state` endpoint
- Nanoleaf colour accuracy: corrected `set_color()` call signature; fixed invalid `duration` on hue/sat calls
- Nanoleaf master brightness: propagated from global brightness range setting
- Nanoleaf IP filtered from LIFX discovery results
- EXE bundle: resolved `lifxlan` import failures in frozen builds (bundled `bitstring` submodules)
- Debug Timing toggle added to Settings for CMD-only performance profiling

---

## [0.1.1] — 2026-06-19

### Added
- Debug Timing toggle in Settings — logs per-effect latency to the terminal
- Log panel: text is now selectable; Copy All button copies the full log to clipboard

### Fixed
- Effect lag with multiple lights: `set_color_all` now sends commands in parallel with `rapid=True`
- Effect lag: event-driven effects bypass the stagger delay entirely
- `safe_label` was making a blocking network call on every invocation — replaced with a cached lookup
- Effect timings recalibrated after safe_label latency was removed
- UDP listen address ignored saved settings — IP and port now wired through to socket bind
- `set_listen_address` failed to rebind socket on IP/port change
- Blue flag and red flag now pulse instead of holding a static colour

---

## [0.1.0] — 2026-06-19

Initial release.

### Added
- UDP telemetry listener for F1 25 (port 20777)
- LIFX LAN discovery and control
- Nine race effects: Start Lights, Lights Out, Yellow Flag, Blue Flag, Red Flag, Fastest Lap, Chequered Flag, White Warning, Neutral
- Multizone strip support — start lights sweep fills zones left-to-right or right-to-left
- Stagger mode — fire each bulb with a configurable delay
- Master brightness range (min/max scaling)
- Idle mode — custom colour with optional slow pulse
- Profiles — save and switch complete configurations
- Light Assignment — assign specific lights to specific effects
- Identify button — flashes a single bulb to confirm which physical light it is
- UDP forwarding — relay packets to a second destination
- Live packet and event log
- Built-in tutorial overlay
- Persistent settings (`f1lifx_gui_settings.json`, `lifx_groups.json`)
- Fixed: localStorage wiped on every launch (setup flow re-ran on each start)
