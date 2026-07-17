"""Byte-accurate packet builders for the dispatch tests.

Every offset here mirrors the parsers in bridge_core.py (F1) and dr2_bridge.py
(DiRT Rally 2.0). If a header layout or field offset changes, these builders (and
the tests that use them) should fail loudly — which is the point.

The F1 builders are re-exported from replay_packets.py rather than defined here:
the in-app effect replays send those same packets, and tests/ is never bundled
into a release build, so they have to live outside tests/. Importing them keeps
one definition behind both.
"""
import os
import struct
import sys

# Same bootstrap as harness.py — allow `unittest discover -s tests` from the repo
# root to import the app modules that live one level up.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from replay_packets import (  # noqa: E402,F401  — re-exported for the tests
    PACKET_ID_SESSION, PACKET_ID_EVENT, PACKET_ID_CAR_TELEMETRY,
    PACKET_ID_CAR_STATUS,
    f1_header, f1_event, f1_start_lights, f1_lights_out, f1_chequered_flag,
    f1_red_flag, f1_fastest_lap, f1_penalty, f1_retirement, f1_car_status_fia,
    f1_car_telemetry, f1_session_marshal, f1_session_zones,
)


# ── DiRT Rally 2.0 (extradata=3, 264 bytes, 66 × f32) ────────────────────────
_DR2_FMT = "<66f"


def dr2_packet(lap_time=0.0, speed=0.0, g_lat=0.0, g_lon=0.0,
               sector=0, last_lap_time=0.0):
    f = [0.0] * 66
    f[1]  = lap_time        # stage timer
    f[7]  = speed           # forward speed m/s
    f[34] = g_lat           # lateral G
    f[35] = g_lon           # longitudinal G
    f[48] = float(sector)   # split index
    f[62] = last_lap_time   # populates at finish
    return struct.pack(_DR2_FMT, *f)


# ── Assetto Corsa shared memory (#49) ────────────────────────────────────────
# Built as the real ctypes structs rather than hand-packed bytes: the graphics
# layout has wchar_t arrays that force compiler padding, so hand-packing would
# bake in the very offset mistake ac_bridge.py uses ctypes to avoid.
def ac_physics(rpms=4000, speed_kmh=120.0, gear=3, g_lat=0.0, g_lon=0.0):
    """accG is (lateral, vertical, longitudinal) — vertical stays 0 because
    kerbs and bumps are exactly what crash detection must not fire on."""
    from ac_bridge import ACPhysics
    p = ACPhysics(packetId=1, gear=gear, rpms=int(rpms), speedKmh=float(speed_kmh))
    p.accG[0], p.accG[1], p.accG[2] = float(g_lat), 0.0, float(g_lon)
    return p


def ac_graphics(status=2, session=2, flag=0, completed_laps=1, penalty_time=0.0,
                best_time_ms=0, current_time_ms=1):
    """status/session/flag default to a live race under no flag.

    See ac_bridge for the enums: status 2 = AC_LIVE, session 2 = AC_RACE.
    best_time_ms 0 is AC's "no lap set yet" sentinel. current_time_ms is the
    running lap timer — 0 means stopped, which is what the grid countdown looks
    like and is how the race start is detected.
    """
    from ac_bridge import ACGraphics
    return ACGraphics(packetId=1, status=status, session=session, flag=flag,
                      completedLaps=completed_laps, penaltyTime=float(penalty_time),
                      iBestTime=int(best_time_ms), iCurrentTime=int(current_time_ms))


# ── Forza "Data Out" (FH5 / FH6 / Forza Motorsport, little-endian) ───────────
# Sled offsets: IsRaceOn @0 (s32), EngineMaxRpm @8 (f32), CurrentRpm @16 (f32).
# Sizes: 232 = Sled (all titles), 311 = Car Dash (Horizon 5 / Motorsport),
# 323–339 = Horizon 6 Car Dash.
#
# Offset 236 is the whole reason the sizes matter: in FH6 it's SmashableVelDiff
# (a collision delta that rests at 0), and in the 311-byte Car Dash it's
# PositionY (a world coordinate that is routinely far above the crash
# threshold). `position_y` exists so tests can prove a Horizon 5 packet can't be
# mistaken for a crash.
def forza_packet(is_race_on=1, current_rpm=4000.0, max_rpm=7000.0,
                 smash_veldiff=0.0, position_y=0.0, size=339):
    buf = bytearray(size)
    struct.pack_into('<i', buf, 0, int(is_race_on))
    struct.pack_into('<f', buf, 8, float(max_rpm))
    struct.pack_into('<f', buf, 16, float(current_rpm))
    if size >= 323:                                  # FH6 SmashableVelDiff field
        struct.pack_into('<f', buf, 236, float(smash_veldiff))
    elif size >= 240:                                # Car Dash: 236 is PositionY
        struct.pack_into('<f', buf, 236, float(position_y))
    return bytes(buf)
