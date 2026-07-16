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

The same replay is available inside the app under Settings -> Advanced; this is
the CLI front-end to it, and both share replay.py.

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
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import replay  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Replay a full-gearbox RPM sweep for the RPM meter.")
    ap.add_argument("--host", default="127.0.0.1", help="bridge listen IP (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=20777, help="bridge UDP port (default 20777)")
    ap.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier (default 1.0)")
    ap.add_argument("--loop", action="store_true", help="repeat the sweep until Ctrl+C")
    args = ap.parse_args()

    print(f"Sending an RPM sweep to {args.host}:{args.port}")
    print("Make sure the bridge is running with the RPM Meter enabled.\n")

    ctx = replay.Context(args.host, args.port, print, lambda: False)
    try:
        while True:
            replay.run_rpm_meter(ctx, speed=args.speed)
            if not args.loop:
                break
            print("  -- looping --")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
