"""Replay crafted F1 Session packets to test live sector status without the game.

Sends UDP Session packets (port 20777 by default) whose marshal-zone flags change
across the three sectors, so you can watch a multizone strip respond. Exercises the
real path: UDP -> parse_session_sector_flags -> sector_status -> your lights.

Usage:
    1. Launch GridGlow, select F1, and turn ON Auto-Response -> "Sector Status".
    2. Start the bridge.
    3. Run this script:   python tools/replay_sector_status.py
       (defaults to 127.0.0.1:20777; pass --host / --port / --delay / --loop)

Each step prints what it's sending so you can match it to the strip.
"""
import argparse
import os
import socket
import sys
import time

# Reuse the exact packet builders the dispatch tests use (single source of truth).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.fixtures import f1_session_zones, f1_red_flag, f1_lights_out  # noqa: E402

# FIA flag values (match bridge_core)
NONE, GREEN, BLUE, YELLOW = 0, 1, 2, 3

# Named Event-packet builders (vs. Session marshal-zone packets).
EVENT_BUILDERS = {
    "LIGHTS_OUT": f1_lights_out,   # marks the race as started (enables flag flashes)
    "RED_FLAG":   f1_red_flag,
}

# Each step is (label, payload). payload is either a list of (zone_start, flag)
# marshal zones (a Session packet) or a key in EVENT_BUILDERS (an Event packet).
# zone_start picks the sector: < 1/3 = S1, < 2/3 = S2, else S3.
SEQUENCE = [
    ("Race start (lights out)",   "LIGHTS_OUT"),
    ("All clear (green S1/S2/S3)", [(0.1, GREEN), (0.5, GREEN), (0.9, GREEN)]),
    ("Yellow in SECTOR 1",        [(0.1, YELLOW), (0.5, GREEN), (0.9, GREEN)]),
    ("Yellow in SECTOR 2",        [(0.1, GREEN), (0.5, YELLOW), (0.9, GREEN)]),
    ("Yellow in SECTOR 3",        [(0.1, GREEN), (0.5, GREEN), (0.9, YELLOW)]),
    ("Yellow in S1 + S3",         [(0.1, YELLOW), (0.5, GREEN), (0.9, YELLOW)]),
    ("Blue in SECTOR 2",          [(0.1, GREEN), (0.5, BLUE), (0.9, GREEN)]),
    ("RED FLAG (whole strip red)", "RED_FLAG"),
    ("All clear again (sectors resume)", [(0.1, GREEN), (0.5, GREEN), (0.9, GREEN)]),
]


def main():
    ap = argparse.ArgumentParser(description="Replay F1 sector-status Session packets.")
    ap.add_argument("--host", default="127.0.0.1", help="bridge listen IP (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=20777, help="bridge UDP port (default 20777)")
    ap.add_argument("--delay", type=float, default=2.5, help="seconds between steps (default 2.5)")
    ap.add_argument("--loop", action="store_true", help="repeat the sequence until Ctrl+C")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"Sending sector-status Session packets to {args.host}:{args.port}")
    print("Make sure the bridge is running with Sector Status enabled.\n")

    try:
        while True:
            for label, payload in SEQUENCE:
                pkt = (EVENT_BUILDERS[payload]() if isinstance(payload, str)
                       else f1_session_zones(payload))
                # UDP can drop; send a few so the change is seen.
                for _ in range(3):
                    sock.sendto(pkt, (args.host, args.port))
                    time.sleep(0.05)
                print(f"  -> {label}")
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
