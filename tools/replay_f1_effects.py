"""Replay crafted F1 packets so every F1 effect fires without running the game.

Sends the same UDP packets F1 broadcasts (port 20777 by default), so each effect
runs through the real path: UDP -> parse/dispatch -> your lights. Handy for
eyeballing multizone vs bulb vs Nanoleaf/Hue behaviour and for tuning intensity
curves.

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
import socket
import sys
import time

# Reuse the exact packet builders the dispatch tests use (single source of truth).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import fixtures as fx  # noqa: E402

# FIA flag values (match bridge_core).
NONE, GREEN, BLUE, YELLOW = 0, 1, 2, 3

# Penalty infringement types that map to an effect (match bridge_core).
INFRINGEMENT_WARNING      = 7    # -> white_warning
INFRINGEMENT_DISQUALIFIED = 44   # -> black_flag

# Seconds between each of the five start lights (the real sequence is ~1s apart).
START_LIGHT_GAP = 0.9


def _start_lights(send):
    """The real build-up: five lights come on one at a time, then it's lights out."""
    for n in range(1, 6):
        send(fx.f1_start_lights(n))
        print(f"       light {n} of 5")
        time.sleep(START_LIGHT_GAP)


# (key, label, sender). Ordered like a race so the sequence reads naturally.
EFFECTS = [
    ("start_lights",   "Start Lights - five-light build-up", _start_lights),
    ("lights_out",     "Lights Out - race start",            lambda s: s(fx.f1_lights_out())),
    ("yellow_flag",    "Yellow Flag - safety car",           lambda s: s(fx.f1_car_status_fia(YELLOW))),
    ("blue_flag",      "Blue Flag - lapped traffic",         lambda s: s(fx.f1_car_status_fia(BLUE))),
    ("red_flag",       "Red Flag - session suspended",       lambda s: s(fx.f1_red_flag())),
    ("fastest_lap",    "Fastest Lap - player car",           lambda s: s(fx.f1_fastest_lap(vehicle_idx=0, player_idx=0))),
    ("chequered_flag", "Chequered Flag - race end",          lambda s: s(fx.f1_chequered_flag())),
    ("white_warning",  "White Warning - penalty",            lambda s: s(fx.f1_penalty(INFRINGEMENT_WARNING))),
    ("black_flag",     "Black Flag - disqualification",      lambda s: s(fx.f1_penalty(INFRINGEMENT_DISQUALIFIED))),
    ("neutral",        "Neutral - track clear",              lambda s: s(fx.f1_car_status_fia(GREEN))),
]
BY_KEY = {key: step for step in EFFECTS for key in (step[0],)}


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
        for key, label, _ in EFFECTS:
            print(f"  {key:<15} {label}")
        return

    if args.effect and not args.all:
        unknown = [e for e in args.effect if e not in BY_KEY]
        if unknown:
            ap.error(f"unknown effect(s): {', '.join(unknown)}. Try --list.")
        steps = [BY_KEY[e] for e in args.effect]
    else:
        steps = EFFECTS

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(pkt):
        # UDP can drop; send a few so the effect is always seen.
        for _ in range(3):
            sock.sendto(pkt, (args.host, args.port))
            time.sleep(0.05)

    print(f"Sending F1 packets to {args.host}:{args.port}")
    print("Make sure GridGlow is running with an F1 title selected and the bridge started.\n")

    try:
        while True:
            for _key, label, fn in steps:
                print(f"  -> {label}")
                fn(send)
                time.sleep(args.delay)
            if not args.loop:
                break
            print("  -- looping --")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
