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

    # ── Live sector status (opt-in; #12) ─────────────────────────────────────
    def test_sector_status_fires_when_enabled(self):
        self.bridge.enabled_events = frozenset({"sector_status"})
        pkt = fx.f1_session_zones([(0.5, 3)])   # yellow in sector 2
        self.assertEqual(self.feed(pkt), [("sector_status", ([0, 3, 0],))])

    def test_sector_status_repaints_only_on_change(self):
        self.bridge.enabled_events = frozenset({"sector_status"})
        pkt = fx.f1_session_zones([(0.5, 3)])
        self.feed(pkt)                       # first paint
        self.bridge.dispatches.clear()
        self.feed(pkt)                       # identical packet → no repaint
        self.assertEqual(self.bridge.dispatches, [])

    def test_sector_status_off_by_default_keeps_flash(self):
        # enabled_events None → sector status inactive, marshal flash unchanged
        self.bridge.race_started = True
        self.assertEqual(self.feed(fx.f1_session_marshal(3)), [("yellow_flag", ())])

    def test_car_status_flash_runs_alongside_sector_status(self):
        # The player-flag flash keeps firing for non-multizone lights while sector
        # status is active — the strip is protected at the controller level, not by
        # suppressing dispatch.
        self.bridge.enabled_events = frozenset({"sector_status", "yellow_flag"})
        self.assertEqual(self.feed(fx.f1_car_status_fia(3)), [("yellow_flag", ())])

    def test_sector_status_and_flash_both_fire_during_race(self):
        # During a race, a marshal change paints the sectors AND flashes the
        # non-multizone lights — sector_status first, then the flag flash.
        self.bridge.enabled_events = frozenset({"sector_status", "yellow_flag"})
        self.bridge.race_started = True
        self.assertEqual(
            self.feed(fx.f1_session_zones([(0.5, 3)])),
            [("sector_status", ([0, 3, 0],)), ("yellow_flag", ())])

    # ── Live RPM meter (opt-in; #12) ─────────────────────────────────────────
    def test_rpm_meter_fires_when_enabled(self):
        self.bridge.enabled_events = frozenset({"rpm_meter"})
        self.assertEqual(self.feed(fx.f1_car_telemetry(50)),
                         [("rpm_meter", (50,))])

    def test_rpm_meter_off_by_default(self):
        # enabled_events None → opt-in RPM meter inactive
        self.assertEqual(self.feed(fx.f1_car_telemetry(50)), [])

    def test_rpm_meter_repaints_only_on_bucket_change(self):
        self.bridge.enabled_events = frozenset({"rpm_meter"})
        self.feed(fx.f1_car_telemetry(50))        # bucket 8 → paints
        self.bridge.dispatches.clear()
        self.feed(fx.f1_car_telemetry(53))        # still bucket 8 → no repaint
        self.assertEqual(self.bridge.dispatches, [])
        self.assertEqual(self.feed(fx.f1_car_telemetry(75)),  # bucket 12 → repaints
                         [("rpm_meter", (75,))])

    def test_rpm_meter_suppressed_during_start_sequence(self):
        self.bridge.enabled_events = frozenset({"rpm_meter"})
        self.feed(fx.f1_start_lights(3))          # start lights own the strip
        self.bridge.dispatches.clear()
        self.assertEqual(self.feed(fx.f1_car_telemetry(90)), [])   # suppressed
        self.feed(fx.f1_lights_out())             # lights out ends the sequence
        self.bridge.dispatches.clear()
        self.assertEqual(self.feed(fx.f1_car_telemetry(90)),
                         [("rpm_meter", (90,))])

    def test_rpm_meter_yields_to_sector_status(self):
        # Both enabled → sector status wins; the RPM meter yields (one strip mode).
        self.bridge.enabled_events = frozenset({"rpm_meter", "sector_status"})
        self.assertEqual(self.feed(fx.f1_car_telemetry(90)), [])

    def test_rpm_meter_blinks_at_redline(self):
        # Top bucket (rev limiter) hands off to the self-sustaining blink loop.
        self.bridge.enabled_events = frozenset({"rpm_meter"})
        self.assertEqual(self.feed(fx.f1_car_telemetry(100)),
                         [("rpm_redline", ())])

    def test_rpm_meter_redline_fires_once_then_resumes_fill(self):
        self.bridge.enabled_events = frozenset({"rpm_meter"})
        self.feed(fx.f1_car_telemetry(100))       # enter redline → blink
        self.bridge.dispatches.clear()
        self.feed(fx.f1_car_telemetry(100))       # still redline → nothing new
        self.assertEqual(self.bridge.dispatches, [])
        self.assertEqual(self.feed(fx.f1_car_telemetry(60)),  # drop out → fill
                         [("rpm_meter", (60,))])

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
