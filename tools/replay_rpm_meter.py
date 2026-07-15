"""Replay a full-lap RPM sweep to test the RPM meter without the game.

Sends UDP Car Telemetry packets (port 20777 by default) that emulate one pull
through the whole gearbox, so you can watch a multizone strip fill green->red:

    1. Idle for 3 seconds (engine ticking over).
    2. Flat out from a standing start, upshifting 1st -> 8th — the revs climb to
       the redline in each gear, drop on the upshift, and climb again.
    3. Bounce off the rev limiter at the top of 8th for 3 seconds (top speed).
    4. Roll back down to idle, downshifting 8th -> 1st with a rev-match blip on
       each downshift, settling to idle in 1st.

Exercises the real path: UDP -> parse_player_car_telemetry -> rpm_meter -> lights.

Usage:
    1. Launch GridGlow, select F1, and turn ON Auto-Response -> "RPM Meter".
    2. Start the bridge.
    3. Run this script:   python tools/replay_rpm_meter.py
       (defaults to 127.0.0.1:20777; pass --host / --port / --loop / --speed)

The meter only repaints when the quantised rev level changes, so tiny wobbles at
a steady rev won't move the strip — that's the LAN throttle doing its job.
"""
import argparse
import os
import socket
import sys
import time

# Reuse the exact packet builder the dispatch tests use (single source of truth).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.fixtures import f1_car_telemetry  # noqa: E402

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
# The trend falls to idle while every downshift kicks the needle back up.
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


def make_sender(sock, host, port):
    def emit(rev_pct, rpm):
        pkt = f1_car_telemetry(int(round(max(0, min(100, rev_pct)))), engine_rpm=int(rpm))
        # UDP can drop; send each frame twice so level changes are seen.
        sock.sendto(pkt, (host, port))
        sock.sendto(pkt, (host, port))
    return emit


def hold(emit, rev_pct, rpm, duration, frame):
    end = time.time() + duration
    while time.time() < end:
        emit(rev_pct, rpm)
        time.sleep(frame)


def ramp(emit, p0, p1, r0, r1, duration, frame):
    steps = max(1, int(duration / frame))
    for i in range(steps + 1):
        t = i / steps
        emit(p0 + (p1 - p0) * t, r0 + (r1 - r0) * t)
        time.sleep(frame)


def limiter(emit, duration, frame):
    """Hold flat against the rev limiter at 100%. The meter's own redline effect
    does the rapid shift-light blink — the telemetry just pins the revs there,
    like a real car bouncing off the limiter at top speed."""
    hold(emit, 100, REDLINE_RPM, duration, frame)


def run_once(emit, frame):
    print(f"  [idle]      {3.0:.0f}s ticking over at {IDLE_PCT}%")
    hold(emit, IDLE_PCT, IDLE_RPM, 3.0, frame)

    prev_pct, prev_rpm = IDLE_PCT, IDLE_RPM
    for gear, base_pct, base_rpm, secs in UPSHIFTS:
        # Upshift: revs snap from wherever we were down to this gear's base.
        print(f"  [gear {gear}]    {base_pct:3d}% -> 100%  flat out")
        emit(base_pct, base_rpm)
        ramp(emit, base_pct, 100, base_rpm, REDLINE_RPM, secs, frame)
        prev_pct, prev_rpm = 100, REDLINE_RPM

    print(f"  [limiter]   {3.0:.0f}s bouncing off the rev limiter at top speed")
    limiter(emit, 3.0, frame)

    print("  [down]      8th -> 1st, downshifting back to idle")
    prev_pct, prev_rpm = 100, REDLINE_RPM
    for gear, fall_pct, blip_pct in DOWNSHIFTS:
        # Revs fall as you lift/brake in this gear...
        fall_rpm = REDLINE_RPM * fall_pct / 100
        ramp(emit, prev_pct, fall_pct, prev_rpm, fall_rpm, 0.45, frame)
        # ...then a downshift rev-matches with a blip up (skip below 1st).
        if gear > 1:
            blip_rpm = REDLINE_RPM * blip_pct / 100
            ramp(emit, fall_pct, blip_pct, fall_rpm, blip_rpm, 0.2, frame)
            prev_pct, prev_rpm = blip_pct, blip_rpm
        else:
            prev_pct, prev_rpm = fall_pct, fall_rpm

    print(f"  [idle]      settled in 1st at {IDLE_PCT}%")
    ramp(emit, prev_pct, IDLE_PCT, prev_rpm, IDLE_RPM, 0.6, frame)
    hold(emit, IDLE_PCT, IDLE_RPM, 1.0, frame)


def main():
    ap = argparse.ArgumentParser(description="Replay a full-gearbox RPM sweep for the RPM meter.")
    ap.add_argument("--host", default="127.0.0.1", help="bridge listen IP (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=20777, help="bridge UDP port (default 20777)")
    ap.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier (default 1.0)")
    ap.add_argument("--loop", action="store_true", help="repeat the sweep until Ctrl+C")
    args = ap.parse_args()

    frame = FRAME / max(0.1, args.speed)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    emit = make_sender(sock, args.host, args.port)

    print(f"Sending an RPM sweep to {args.host}:{args.port}")
    print("Make sure the bridge is running with the RPM Meter enabled.\n")

    try:
        while True:
            run_once(emit, frame)
            if not args.loop:
                break
            print("  -- looping --")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
