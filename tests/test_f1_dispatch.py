"""F1 25 packet → effect dispatch integration tests."""
import unittest

from tests import fixtures as fx
from tests.harness import RecordingF1Bridge


class F1DispatchTests(unittest.TestCase):
    def setUp(self):
        self.bridge = RecordingF1Bridge()

    def feed(self, *packets):
        for p in packets:
            self.bridge.handle_packet(p)
        return self.bridge.dispatches

    # ── Start sequence ───────────────────────────────────────────────────────
    def test_start_lights_passes_count(self):
        self.assertEqual(self.feed(fx.f1_start_lights(5)),
                         [("start_lights", (5,))])

    def test_lights_out(self):
        self.assertEqual(self.feed(fx.f1_lights_out()),
                         [("lights_out", ())])

    # ── Discrete flags ───────────────────────────────────────────────────────
    def test_chequered_flag(self):
        self.assertEqual(self.feed(fx.f1_chequered_flag()),
                         [("chequered_flag", ())])

    def test_red_flag_event(self):
        self.assertEqual(self.feed(fx.f1_red_flag()),
                         [("red_flag", ())])

    # ── Fastest lap is player-gated ──────────────────────────────────────────
    def test_fastest_lap_player(self):
        self.assertEqual(self.feed(fx.f1_fastest_lap(vehicle_idx=0, player_idx=0)),
                         [("fastest_lap", ())])

    def test_fastest_lap_other_car_ignored(self):
        self.assertEqual(self.feed(fx.f1_fastest_lap(vehicle_idx=5, player_idx=0)),
                         [])

    # ── Penalties → black flag / white warning ───────────────────────────────
    def test_penalty_black_flag(self):
        # infringement 44 = black flag timer
        self.assertEqual(self.feed(fx.f1_penalty(44)),
                         [("black_flag", ())])

    def test_penalty_white_warning(self):
        # infringement 7 = corner cutting gained time
        self.assertEqual(self.feed(fx.f1_penalty(7)),
                         [("white_warning", ())])

    def test_penalty_unmapped_infringement_ignored(self):
        # infringement 1 maps to neither set → nothing fires
        self.assertEqual(self.feed(fx.f1_penalty(1)), [])

    # ── Retirement reasons ───────────────────────────────────────────────────
    def test_retirement_black_flag(self):
        self.assertEqual(self.feed(fx.f1_retirement(reason=6)),
                         [("black_flag", ())])

    def test_retirement_red_flag(self):
        self.assertEqual(self.feed(fx.f1_retirement(reason=7)),
                         [("red_flag", ())])

    # ── FIA flags from car-status packets ────────────────────────────────────
    def test_car_status_yellow(self):
        self.assertEqual(self.feed(fx.f1_car_status_fia(3)),
                         [("yellow_flag", ())])

    def test_car_status_blue(self):
        self.assertEqual(self.feed(fx.f1_car_status_fia(2)),
                         [("blue_flag", ())])

    def test_car_status_green_is_neutral(self):
        self.assertEqual(self.feed(fx.f1_car_status_fia(1)),
                         [("neutral", ())])

    # ── FIA flags from session marshal zones (needs race started) ────────────
    def test_session_marshal_yellow(self):
        self.bridge.race_started = True
        self.assertEqual(self.feed(fx.f1_session_marshal(3)),
                         [("yellow_flag", ())])

    def test_session_marshal_ignored_before_race_start(self):
        # race_started defaults False → marshal flags suppressed
        self.assertEqual(self.feed(fx.f1_session_marshal(3)), [])

    # ── Cross-cutting behaviour ──────────────────────────────────────────────
    def test_unsupported_packet_format_ignored(self):
        pkt = fx.f1_start_lights(5)
        bad = (2099).to_bytes(2, "little") + pkt[2:]   # unknown format
        self.assertEqual(self.feed(bad), [])

    def test_disabled_event_does_not_fire(self):
        self.bridge.enabled_events = frozenset({"lights_out"})  # start_lights off
        self.assertEqual(self.feed(fx.f1_start_lights(5)), [])
        self.assertEqual(self.feed(fx.f1_lights_out()), [("lights_out", ())])


if __name__ == "__main__":
    unittest.main(verbosity=2)
