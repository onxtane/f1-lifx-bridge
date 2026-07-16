"""The effect replays, behind both Settings -> Advanced and tools/replay_*.py.

Each replay sends crafted F1 UDP packets to a running bridge, so an effect fires
through the real path — UDP -> parse/dispatch -> your lights — without launching
the game. Useful for eyeballing multizone vs bulb vs Nanoleaf/Hue behaviour and
for tuning effects against real hardware.

One implementation, two front-ends: the CLI scripts in tools/ and the in-app
buttons both call run() here, so what you test from a terminal is what the app
does. The packets come from replay_packets.py, which the dispatch tests also
build with, so a replay can't drift from the real packet format either.

A replay runs for ~20-35 seconds, so every wait is interruptible: the app runs
these on a background thread and Stop has to land promptly rather than at the
end of the current step.
"""
import socket
import time
from typing import Callable, List, NamedTuple

import replay_packets as pk

# FIA flag values (match bridge_core).
NONE, GREEN, BLUE, YELLOW = 0, 1, 2, 3

# Penalty infringement types that map to an effect (match bridge_core).
INFRINGEMENT_WARNING      = 7    # -> white_warning
INFRINGEMENT_DISQUALIFIED = 44   # -> black_flag

# Seconds between each of the five start lights (the real sequence is ~1s apart).
START_LIGHT_GAP = 0.9

# RPM sweep shape.
IDLE_PCT, IDLE_RPM = 8, 4000
REDLINE_RPM = 12800
FRAME = 1.0 / 60       # seconds between telemetry frames — real F1 rate (60 Hz)

# Accelerating flat out: each gear climbs from its post-upshift rev level to the
# redline (100%). The reset level rises through the box as the ratios close up.
# (gear, rev% just after the upshift, engine rpm at that point, climb seconds)
UPSHIFTS = [
    (1, 15, 5000, 0.7),
    (2, 45, 8000, 0.7),
    (3, 55, 9000, 0.8),
    (4, 62, 9800, 0.9),
    (5, 68, 10400, 1.0),
    (6, 73, 10900, 1.1),
    (7, 77, 11400, 1.2),
    (8, 80, 11800, 1.4),
]

# Rolling back down to idle, downshifting 8th -> 1st. Each pair is
# (rev% as the revs fall in the gear, rev% blip on the downshift rev-match).
DOWNSHIFTS = [
    (8, 70, 84),
    (7, 60, 78),
    (6, 55, 75),
    (5, 52, 72),
    (4, 48, 70),
    (3, 44, 66),
    (2, 38, 55),
    (1, 28, 40),
]

# Sector-status steps. Payload is either marshal zones (a Session packet) or a
# key in _EVENT_BUILDERS (an Event packet). zone_start picks the sector:
# < 1/3 = S1, < 2/3 = S2, else S3.
_EVENT_BUILDERS = {
    "LIGHTS_OUT": pk.f1_lights_out,   # marks the race started (enables flag flashes)
    "RED_FLAG":   pk.f1_red_flag,
}

SECTOR_SEQUENCE = [
    ("Race start (lights out)",           "LIGHTS_OUT"),
    ("All clear (green S1/S2/S3)",        [(0.1, GREEN),  (0.5, GREEN),  (0.9, GREEN)]),
    ("Yellow in SECTOR 1",                [(0.1, YELLOW), (0.5, GREEN),  (0.9, GREEN)]),
    ("Yellow in SECTOR 2",                [(0.1, GREEN),  (0.5, YELLOW), (0.9, GREEN)]),
    ("Yellow in SECTOR 3",                [(0.1, GREEN),  (0.5, GREEN),  (0.9, YELLOW)]),
    ("Yellow in S1 + S3",                 [(0.1, YELLOW), (0.5, GREEN),  (0.9, YELLOW)]),
    ("Blue in SECTOR 2",                  [(0.1, GREEN),  (0.5, BLUE),   (0.9, GREEN)]),
    ("RED FLAG (whole strip red)",        "RED_FLAG"),
    ("All clear again (sectors resume)",  [(0.1, GREEN),  (0.5, GREEN),  (0.9, GREEN)]),
]


class Stopped(Exception):
    """Raised inside a replay when the caller has asked it to stop."""


class Context:
    """What a replay needs: somewhere to send, something to report to, and a
    way to be interrupted mid-flight."""

    def __init__(self, host: str, port: int,
                 log: Callable[[str], None],
                 should_stop: Callable[[], bool]):
        self.host = host
        self.port = int(port)
        self.log = log
        self._should_stop = should_stop
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def check(self):
        if self._should_stop():
            raise Stopped

    def send(self, packet: bytes, times: int = 3, gap: float = 0.05):
        """UDP can drop, so send a few — an effect that didn't fire reads as a bug."""
        for _ in range(times):
            self.check()
            self._sock.sendto(packet, (self.host, self.port))
            if gap:
                self.sleep(gap)

    def sleep(self, seconds: float):
        """Wait, but notice a stop request while waiting."""
        end = time.monotonic() + seconds
        while True:
            self.check()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass


# ── F1 effects ───────────────────────────────────────────────────────────────

def _start_lights(ctx, delay):
    """The real build-up: five lights come on one at a time, then it's lights out."""
    for n in range(1, 6):
        ctx.send(pk.f1_start_lights(n))
        ctx.log(f"    light {n} of 5")
        ctx.sleep(START_LIGHT_GAP)


def _simple(builder):
    def step(ctx, delay):
        ctx.send(builder())
    return step


# (key, label, sender). Ordered like a race so the sequence reads naturally.
F1_EFFECTS = [
    ("start_lights",   "Start Lights - five-light build-up", _start_lights),
    ("lights_out",     "Lights Out - race start",            _simple(pk.f1_lights_out)),
    ("yellow_flag",    "Yellow Flag - safety car",           _simple(lambda: pk.f1_car_status_fia(YELLOW))),
    ("blue_flag",      "Blue Flag - lapped traffic",         _simple(lambda: pk.f1_car_status_fia(BLUE))),
    ("red_flag",       "Red Flag - session suspended",       _simple(pk.f1_red_flag)),
    ("fastest_lap",    "Fastest Lap - player car",           _simple(lambda: pk.f1_fastest_lap(vehicle_idx=0, player_idx=0))),
    ("chequered_flag", "Chequered Flag - race end",          _simple(pk.f1_chequered_flag)),
    ("white_warning",  "White Warning - penalty",            _simple(lambda: pk.f1_penalty(INFRINGEMENT_WARNING))),
    ("black_flag",     "Black Flag - disqualification",      _simple(lambda: pk.f1_penalty(INFRINGEMENT_DISQUALIFIED))),
    ("neutral",        "Neutral - track clear",              _simple(lambda: pk.f1_car_status_fia(GREEN))),
]
F1_EFFECTS_BY_KEY = {key: step for step in F1_EFFECTS for key in (step[0],)}


def run_f1_effects(ctx, delay: float = 3.0, only: List[str] = None):
    steps = [F1_EFFECTS_BY_KEY[k] for k in only] if only else F1_EFFECTS
    for _key, label, fn in steps:
        ctx.log(f"  -> {label}")
        fn(ctx, delay)
        ctx.sleep(delay)


# ── RPM meter ────────────────────────────────────────────────────────────────

def _emit(ctx, rev_pct, rpm):
    packet = pk.f1_car_telemetry(int(round(max(0, min(100, rev_pct)))), engine_rpm=int(rpm))
    # Twice per frame, no gap: at 60 Hz an inter-packet sleep would wreck the rate.
    ctx.send(packet, times=2, gap=0)


def _hold(ctx, rev_pct, rpm, duration, frame):
    # Frame-counted rather than clock-watched, like _ramp: how many packets a
    # step sends shouldn't depend on how the sleep happened to land.
    for _ in range(max(1, int(duration / frame))):
        _emit(ctx, rev_pct, rpm)
        ctx.sleep(frame)


def _ramp(ctx, p0, p1, r0, r1, duration, frame):
    steps = max(1, int(duration / frame))
    for i in range(steps + 1):
        t = i / steps
        _emit(ctx, p0 + (p1 - p0) * t, r0 + (r1 - r0) * t)
        ctx.sleep(frame)


def run_rpm_meter(ctx, speed: float = 1.0):
    """One pull through the whole gearbox: idle, flat out 1st-8th, bounce off the
    limiter, then roll back down to idle with a rev-match blip on each downshift.

    `speed` shortens the sweep; it does not change the packet rate. The game
    always sends 60 Hz, and the meter only repaints when the quantised rev level
    changes, so sending faster than 60 Hz would exercise the LAN throttle rather
    than the meter.
    """
    scale = 1.0 / max(0.1, speed)
    frame = FRAME

    ctx.log(f"  [idle]     ticking over at {IDLE_PCT}%")
    _hold(ctx, IDLE_PCT, IDLE_RPM, 3.0 * scale, frame)

    for gear, base_pct, base_rpm, secs in UPSHIFTS:
        ctx.log(f"  [gear {gear}]   {base_pct:3d}% -> 100%  flat out")
        _emit(ctx, base_pct, base_rpm)
        _ramp(ctx, base_pct, 100, base_rpm, REDLINE_RPM, secs * scale, frame)

    # Pinned at 100% — the meter's own redline effect does the shift-light blink,
    # like a real car bouncing off the limiter at top speed.
    ctx.log("  [limiter]  bouncing off the rev limiter at top speed")
    _hold(ctx, 100, REDLINE_RPM, 3.0 * scale, frame)

    ctx.log("  [down]     8th -> 1st, downshifting back to idle")
    prev_pct, prev_rpm = 100, REDLINE_RPM
    for gear, fall_pct, blip_pct in DOWNSHIFTS:
        fall_rpm = REDLINE_RPM * fall_pct / 100
        _ramp(ctx, prev_pct, fall_pct, prev_rpm, fall_rpm, 0.45 * scale, frame)
        if gear > 1:
            blip_rpm = REDLINE_RPM * blip_pct / 100
            _ramp(ctx, fall_pct, blip_pct, fall_rpm, blip_rpm, 0.2 * scale, frame)
            prev_pct, prev_rpm = blip_pct, blip_rpm
        else:
            prev_pct, prev_rpm = fall_pct, fall_rpm

    ctx.log(f"  [idle]     settled in 1st at {IDLE_PCT}%")
    _ramp(ctx, prev_pct, IDLE_PCT, prev_rpm, IDLE_RPM, 0.6 * scale, frame)
    _hold(ctx, IDLE_PCT, IDLE_RPM, 1.0 * scale, frame)


# ── Sector status ────────────────────────────────────────────────────────────

def run_sector_status(ctx, delay: float = 2.5):
    for label, payload in SECTOR_SEQUENCE:
        packet = (_EVENT_BUILDERS[payload]() if isinstance(payload, str)
                  else pk.f1_session_zones(payload))
        ctx.send(packet)
        ctx.log(f"  -> {label}")
        ctx.sleep(delay)


# ── Registry ─────────────────────────────────────────────────────────────────

class Replay(NamedTuple):
    key: str
    label: str
    blurb: str
    needs: str          # what the user must have enabled for it to show anything
    seconds: int        # rough runtime, so the UI can set expectations
    fn: Callable


REPLAYS = [
    Replay("f1_effects", "Replay F1 effects",
           "Every F1 effect in race order, from the start-light build-up to track clear.",
           "", 35, run_f1_effects),
    Replay("rpm_meter", "Replay RPM meter",
           "One pull through the gearbox: idle, flat out 1st-8th, off the limiter, back down.",
           "the RPM Meter effect", 21, run_rpm_meter),
    Replay("sector_status", "Replay sector status",
           "Yellow, blue and red flags moving across the three sectors.",
           "the Sector Status effect", 24, run_sector_status),
]
BY_KEY = {r.key: r for r in REPLAYS}


def run(key: str, host: str, port: int,
        log: Callable[[str], None],
        should_stop: Callable[[], bool] = lambda: False) -> bool:
    """Run one replay to completion. Returns False if it was stopped early.

    Never raises for a stop — stopping is a normal outcome, not a fault.
    """
    replay = BY_KEY[key]
    ctx = Context(host, port, log, should_stop)
    log(f"[REPLAY] {replay.label} -> {host}:{port} (about {replay.seconds}s)")
    try:
        replay.fn(ctx)
        log(f"[REPLAY] {replay.label} finished")
        return True
    except Stopped:
        log(f"[REPLAY] {replay.label} stopped")
        return False
    finally:
        ctx.close()
