"""EA SPORTS WRC packet -> effect dispatch integration tests.

The WRC session_update packet builder lives here rather than in fixtures.py so
these tests stay self-contained. Offsets mirror wrc_bridge.py (and the shipped
assets/wrc/gridglow.json structure).
"""
import struct
import unittest

from tests.harness import RecordingWRCBridge

_WRC_SIZE = 126  # full session_update packet with the GridGlow channel list


def wrc_packet(stage_time=0.0, status=0, progress=0.0, speed=0.0,
               rpm=0.0, rpm_max=0.0):
    """Build a little-endian EA WRC session_update packet.

    Only the fields wrc_bridge.py reads are populated; everything else is zero.
    """
    b = bytearray(_WRC_SIZE)
    b[0:4] = b"SESU"                                  # packet_4cc
    struct.pack_into("<f", b, 45,  speed)             # vehicle_speed
    struct.pack_into("<f", b, 53,  rpm_max)           # vehicle_engine_rpm_max
    struct.pack_into("<f", b, 61,  rpm)               # vehicle_engine_rpm_current
    struct.pack_into("<f", b, 85,  stage_time)        # stage_current_time
    struct.pack_into("<B", b, 101, status)            # stage_result_status
    struct.pack_into("<f", b, 102, progress)          # stage_progress
    return bytes(b)


class WRCDispatchTests(unittest.TestCase):
    def setUp(self):
        self.bridge = RecordingWRCBridge()

    def feed(self, *packets):
        for p in packets:
            self.bridge._handle_wrc(p)
        return self.bridge.dispatches

    def start_stage(self):
        """Enter an active stage (fires lights_out), then clear dispatches."""
        self.feed(wrc_packet(stage_time=1.0, progress=0.0))
        self.bridge.reset()

    # ── Stage start ──────────────────────────────────────────────────────────
    def test_stage_start_fires_lights_out(self):
        self.assertEqual(self.feed(wrc_packet(stage_time=0.5)),
                         [("lights_out", ())])

    def test_no_start_before_stage_timer_runs(self):
        self.assertEqual(self.feed(wrc_packet(stage_time=0.0)), [])

    def test_start_fires_once_only(self):
        self.assertEqual(
            self.feed(wrc_packet(stage_time=0.5), wrc_packet(stage_time=1.5)),
            [("lights_out", ())])

    # ── Split checkpoints ─────────────────────────────────────────────────────
    def test_splits_fire_at_each_third(self):
        self.start_stage()
        got = self.feed(
            wrc_packet(stage_time=10, progress=0.20),   # still first third
            wrc_packet(stage_time=20, progress=0.40),   # -> second third
            wrc_packet(stage_time=30, progress=0.70),   # -> final third
        )
        self.assertEqual(got, [("fastest_lap", ()), ("fastest_lap", ())])

    def test_split_does_not_repeat_within_a_third(self):
        self.start_stage()
        got = self.feed(
            wrc_packet(stage_time=20, progress=0.40),
            wrc_packet(stage_time=21, progress=0.55),
            wrc_packet(stage_time=22, progress=0.60),
        )
        self.assertEqual(got, [("fastest_lap", ())])

    # ── Stage finish ──────────────────────────────────────────────────────────
    def test_finish_fires_chequered_flag(self):
        self.start_stage()
        self.assertEqual(
            self.feed(wrc_packet(stage_time=90, progress=1.0, status=1)),
            [("chequered_flag", ())])

    def test_finish_fires_once_only(self):
        self.start_stage()
        got = self.feed(
            wrc_packet(stage_time=90, progress=1.0, status=1),
            wrc_packet(stage_time=90, progress=1.0, status=1),
        )
        self.assertEqual(got, [("chequered_flag", ())])

    def test_no_split_after_finish(self):
        self.start_stage()
        got = self.feed(
            wrc_packet(stage_time=90, progress=1.0, status=1),  # finish
            wrc_packet(stage_time=91, progress=1.0, status=1),  # lingering
        )
        self.assertEqual(got, [("chequered_flag", ())])

    # ── Return to service park ────────────────────────────────────────────────
    def test_return_to_service_fires_neutral(self):
        self.start_stage()
        self.assertEqual(self.feed(wrc_packet(stage_time=0.0)),
                         [("neutral", ())])

    def test_full_stage_sequence(self):
        got = self.feed(
            wrc_packet(stage_time=0.0),                         # menu
            wrc_packet(stage_time=1.0,  progress=0.05),         # start
            wrc_packet(stage_time=20.0, progress=0.40),         # split 1
            wrc_packet(stage_time=40.0, progress=0.70),         # split 2
            wrc_packet(stage_time=60.0, progress=1.0, status=1),# finish
            wrc_packet(stage_time=0.0),                         # back to menu
        )
        self.assertEqual(got, [
            ("lights_out", ()),
            ("fastest_lap", ()),
            ("fastest_lap", ()),
            ("chequered_flag", ()),
            ("neutral", ()),
        ])

    # ── Packet validation ─────────────────────────────────────────────────────
    def test_wrong_4cc_ignored(self):
        p = bytearray(wrc_packet(stage_time=1.0))
        p[0:4] = b"XXXX"
        self.assertEqual(self.feed(bytes(p)), [])

    def test_undersized_packet_ignored(self):
        self.assertEqual(self.feed(b"SESU" + b"\x00" * 20), [])

    # ── Event gating ──────────────────────────────────────────────────────────
    def test_disabled_event_suppressed(self):
        self.bridge.enabled_events = ["fastest_lap"]  # lights_out not enabled
        self.assertEqual(self.feed(wrc_packet(stage_time=0.5)), [])


if __name__ == "__main__":
    unittest.main()
