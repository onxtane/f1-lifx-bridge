"""Byte-accurate packet builders for the dispatch tests.

Every offset here mirrors the parsers in bridge_core.py (F1) and dr2_bridge.py
(DiRT Rally 2.0). If a header layout or field offset changes, these builders (and
the tests that use them) should fail loudly — which is the point.
"""
import struct

# ── F1 (F1 25 / 2025, 29-byte header) ────────────────────────────────────────
# _HEADER_FORMAT_2425 = "<HBBBBBQfIIBB"  (bridge_core.py)
_F1_HEADER_FMT = "<HBBBBBQfIIBB"

PACKET_ID_SESSION    = 1
PACKET_ID_EVENT      = 3
PACKET_ID_CAR_STATUS = 7

_CAR_STATUS_DATA_SIZE = 55
_FIA_FLAG_OFFSET      = 28   # within a CarStatusData block


def f1_header(packet_id, player_idx=0, packet_format=2025):
    return struct.pack(
        _F1_HEADER_FMT,
        packet_format,  # H  m_packetFormat
        25,             # B  m_gameYear
        1, 0,           # B  major / B minor
        1,              # B  m_packetVersion
        packet_id,      # B  m_packetId
        0,              # Q  m_sessionUID
        0.0,            # f  m_sessionTime
        0,              # I  m_frameIdentifier
        0,              # I  m_overallFrameIdentifier
        player_idx,     # B  m_playerCarIndex
        255,            # B  m_secondaryPlayerCarIndex
    )


def f1_event(code, details=b"", player_idx=0):
    assert len(code) == 4, "event code must be 4 chars"
    return f1_header(PACKET_ID_EVENT, player_idx) + code.encode("ascii") + details


# Discrete events
def f1_start_lights(num_lights):
    return f1_event("STLG", bytes([num_lights]))           # uint8 numLights


def f1_lights_out():
    return f1_event("LGOT")


def f1_chequered_flag():
    return f1_event("CHQF")


def f1_red_flag():
    return f1_event("RDFL")


def f1_fastest_lap(vehicle_idx, lap_time=83.2, player_idx=0):
    # FTLP details: uint8 vehicleIdx, float lapTime
    return f1_event("FTLP", bytes([vehicle_idx]) + struct.pack("<f", lap_time),
                    player_idx)


def f1_penalty(infringement_type, vehicle_idx=0):
    # PENA details: penaltyType, infringementType, vehicleIdx, otherVehicleIdx,
    #               time, lapNum, placesGained  (7 × uint8)
    return f1_event("PENA", bytes([0, infringement_type, vehicle_idx, 255, 0, 1, 0]))


def f1_retirement(reason, vehicle_idx=0):
    # RTMT details: uint8 vehicleIdx, uint8 reason
    return f1_event("RTMT", bytes([vehicle_idx, reason]))


def f1_car_status_fia(flag, player_idx=0):
    """Car-status packet carrying m_vehicleFiaFlags for the player's car."""
    body = bytearray(_CAR_STATUS_DATA_SIZE * 22)
    off = player_idx * _CAR_STATUS_DATA_SIZE + _FIA_FLAG_OFFSET
    struct.pack_into("<b", body, off, flag)   # int8, signed
    return f1_header(PACKET_ID_CAR_STATUS, player_idx) + bytes(body)


def f1_session_marshal(flag, num_zones=1):
    """Session packet whose first marshal zone carries `flag`.

    Layout relative to header: num_marshal_zones at +18, zones at +19,
    each MarshalZone = float zoneStart + int8 zoneFlag (5 bytes).
    """
    body = bytearray(19 + num_zones * 5 + 4)
    body[18] = num_zones
    for i in range(num_zones):
        struct.pack_into("<b", body, 19 + i * 5 + 4, flag)
    return f1_header(PACKET_ID_SESSION) + bytes(body)


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
