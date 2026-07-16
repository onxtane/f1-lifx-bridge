"""Replay crafted F1 Session packets to test live sector status without the game.

Sends UDP Session packets (port 20777 by default) whose marshal-zone flags change
across the three sectors, so you can watch a multizone strip respond. Exercises the
real path: UDP -> parse_session_sector_flags -> sector_status -> your lights.

The same replay is available inside the app under Settings -> Advanced; this is
the CLI front-end to it, and both share replay.py.

Usage:
    1. Launch GridGlow, select F1, and turn ON Auto-Response -> "Sector Status".
    2. Start the bridge.
    3. Run this script:   python tools/replay_sector_status.py
       (defaults to 127.0.0.1:20777; pass --host / --port / --delay / --loop)

Each step prints what it's sending so you can match it to the strip.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import replay  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Replay F1 sector-status Session packets.")
    ap.add_argument("--host", default="127.0.0.1", help="bridge listen IP (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=20777, help="bridge UDP port (default 20777)")
    ap.add_argument("--delay", type=float, default=2.5, help="seconds between steps (default 2.5)")
    ap.add_argument("--loop", action="store_true", help="repeat the sequence until Ctrl+C")
    args = ap.parse_args()

    print(f"Sending sector-status Session packets to {args.host}:{args.port}")
    print("Make sure the bridge is running with Sector Status enabled.\n")

    ctx = replay.Context(args.host, args.port, print, lambda: False)
    try:
        while True:
            replay.run_sector_status(ctx, delay=args.delay)
            if not args.loop:
                break
            print("  -- looping --")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
