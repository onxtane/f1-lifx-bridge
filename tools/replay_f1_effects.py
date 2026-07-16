"""Replay crafted F1 packets so every F1 effect fires without running the game.

Sends the same UDP packets F1 broadcasts (port 20777 by default), so each effect
runs through the real path: UDP -> parse/dispatch -> your lights. Handy for
eyeballing multizone vs bulb vs Nanoleaf/Hue behaviour and for tuning intensity
curves.

The same replay is available inside the app under Settings -> Advanced; this is
the CLI front-end to it, and both share replay.py.

Usage:
    1. Launch GridGlow, select an F1 title, and start the bridge.
    2. Run this script:

        python tools/replay_f1_effects.py                  # every effect, in order
        python tools/replay_f1_effects.py --list           # show effect names
        python tools/replay_f1_effects.py --effect yellow_flag --effect blue_flag
        python tools/replay_f1_effects.py --delay 4 --loop

Each step prints what it's sending so you can match it to the lights.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import replay  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="Replay F1 packets so every effect fires without running the game.")
    ap.add_argument("--host", default="127.0.0.1", help="bridge listen IP (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=20777, help="bridge UDP port (default 20777)")
    ap.add_argument("--delay", type=float, default=3.0,
                    help="seconds to hold each effect so you can watch it (default 3.0)")
    ap.add_argument("--effect", action="append", metavar="NAME",
                    help="run only this effect (repeatable); default is the full sequence")
    ap.add_argument("--all", action="store_true",
                    help="run the full sequence (the default when no --effect is given)")
    ap.add_argument("--loop", action="store_true", help="repeat until Ctrl+C")
    ap.add_argument("--list", action="store_true", help="list effect names and exit")
    args = ap.parse_args()

    if args.list:
        print("Available effects:\n")
        for key, label, _ in replay.F1_EFFECTS:
            print(f"  {key:<15} {label}")
        return

    only = None
    if args.effect and not args.all:
        unknown = [e for e in args.effect if e not in replay.F1_EFFECTS_BY_KEY]
        if unknown:
            ap.error(f"unknown effect(s): {', '.join(unknown)}. Try --list.")
        only = args.effect

    print(f"Sending F1 packets to {args.host}:{args.port}")
    print("Make sure GridGlow is running with an F1 title selected and the bridge started.\n")

    ctx = replay.Context(args.host, args.port, print, lambda: False)
    try:
        while True:
            replay.run_f1_effects(ctx, delay=args.delay, only=only)
            if not args.loop:
                break
            print("  -- looping --")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
