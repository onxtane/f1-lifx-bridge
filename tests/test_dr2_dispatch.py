"""DiRT Rally 2.0 packet → effect dispatch integration tests.

DR2 dispatch is stateful (a small per-stage state machine), so most tests feed a
short packet sequence: a stage-start packet to establish state, then the packet
under test. dispatches are reset after the setup packet so each test asserts only
the effect it cares about.
"""
import unittest

from tests import fixtures as fx
from tests.harness import RecordingDR2Bridge


class DR2DispatchTests(unittest.TestCase):
    def setUp(self):
        self.bridge = RecordingDR2Bridge()

    def feed(self, *packets):
        for p in packets:
            self.bridge._handle_dr2(p)
        return self.bridge.dispatches

    def start_stage(self, speed=30.0):
        """Enter a running stage (fires lights_out), then clear dispatches."""
        self.feed(fx.dr2_packet(lap_time=5.0, speed=speed, sector=0))
        self.bridge.reset()

    # ── Stage start ──────────────────────────────────────────────────────────
    def test_stage_start_fires_lights_out(self):
        self.assertEqual(self.feed(fx.dr2_packet(lap_time=5.0, sector=0)),
                         [("lights_out", ())])

    # ── Split checkpoint ─────────────────────────────────────────────────────
    def test_split_checkpoint_fires_fastest_lap(self):
        self.start_stage()
        self.assertEqual(self.feed(fx.dr2_packet(lap_time=12.0, sector=1)),
                         [("fastest_lap", ())])

    # ── Stage finish ─────────────────────────────────────────────────────────
    def test_stage_finish_fires_chequered_flag(self):
        self.start_stage()
        self.assertEqual(
            self.feed(fx.dr2_packet(lap_time=90.0, sector=2, last_lap_time=88.4)),
            [("chequered_flag", ())])

    # ── Crash ────────────────────────────────────────────────────────────────
    def test_crash_fires_on_gforce_and_speed_drop(self):
        self.start_stage(speed=30.0)
        # combined G = sqrt(3^2 + 2^2) ≈ 3.6 > 3.5; speed drop 10 m/s > 4
        self.assertEqual(
            self.feed(fx.dr2_packet(lap_time=12.0, speed=20.0, g_lat=3.0, g_lon=2.0)),
            [("crash", ())])

    def test_hard_braking_without_gforce_is_not_a_crash(self):
        self.start_stage(speed=30.0)
        # big speed drop but no G spike → no crash
        self.assertEqual(
            self.feed(fx.dr2_packet(lap_time=12.0, speed=18.0, g_lat=0.2, g_lon=0.3)),
            [])

    def test_gforce_without_speed_drop_is_not_a_crash(self):
        self.start_stage(speed=30.0)
        # high G (cornering) but speed steady → no crash
        self.assertEqual(
            self.feed(fx.dr2_packet(lap_time=12.0, speed=30.0, g_lat=3.0, g_lon=2.0)),
            [])

    # ── Service park ─────────────────────────────────────────────────────────
    def test_return_to_service_park_fires_neutral(self):
        self.start_stage()
        self.assertEqual(self.feed(fx.dr2_packet(lap_time=0.0)),
                         [("neutral", ())])

    # ── Event gating ─────────────────────────────────────────────────────────
    def test_disabled_crash_event_does_not_fire(self):
        self.bridge.enabled_events = frozenset(
            {"lights_out", "fastest_lap", "chequered_flag", "neutral"})  # crash off
        self.start_stage(speed=30.0)
        self.assertEqual(
            self.feed(fx.dr2_packet(lap_time=12.0, speed=20.0, g_lat=3.0, g_lon=2.0)),
            [])

    def test_undersized_packet_ignored(self):
        self.assertEqual(self.feed(b"\x00" * 100), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
