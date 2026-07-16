"""Forza (Data Out) packet → effect dispatch integration tests (FH5/FH6/FM)."""
import unittest

from tests import fixtures as fx
from tests.harness import RecordingForzaBridge


class ForzaDispatchTests(unittest.TestCase):
    def setUp(self):
        self.bridge = RecordingForzaBridge()

    def feed(self, *packets):
        for p in packets:
            self.bridge._handle_forza(p)
        return self.bridge.dispatches

    def start_race(self):
        """Enter an active race (fires lights_out), then clear dispatches."""
        self.feed(fx.forza_packet(is_race_on=1))
        self.bridge.reset()

    # ── Race start / end ─────────────────────────────────────────────────────
    def test_race_start_fires_lights_out(self):
        self.assertEqual(self.feed(fx.forza_packet(is_race_on=1)),
                         [("lights_out", ())])

    def test_race_end_fires_neutral(self):
        self.start_race()
        self.assertEqual(self.feed(fx.forza_packet(is_race_on=0)),
                         [("neutral", ())])

    def test_no_repeat_while_race_stays_on(self):
        self.start_race()
        self.assertEqual(self.feed(fx.forza_packet(is_race_on=1),
                                   fx.forza_packet(is_race_on=1)), [])

    # ── Crash (FH6 SmashableVelDiff) ─────────────────────────────────────────
    def test_crash_fires_on_impact_spike(self):
        self.start_race()
        self.assertEqual(
            self.feed(fx.forza_packet(is_race_on=1, smash_veldiff=12.0)),
            [("crash", ())])

    def test_small_bump_is_not_a_crash(self):
        self.start_race()
        self.assertEqual(
            self.feed(fx.forza_packet(is_race_on=1, smash_veldiff=3.0)), [])

    def test_crash_needs_fh6_sized_packet(self):
        # A 232-byte Sled packet (FH5/FM) has no SmashableVelDiff field → no crash.
        self.start_race()
        self.assertEqual(
            self.feed(fx.forza_packet(is_race_on=1, smash_veldiff=20.0, size=232)),
            [])

    def test_a_sustained_high_value_flashes_once_not_forever(self):
        """A collision delta spikes and falls; a coordinate sits high.

        If the layout is ever wrong and 236 is really a position, an edge
        detector means one stray flash rather than one every cooldown for the
        whole drive (#52).
        """
        self.start_race()
        # Copy: feed() hands back the live dispatches list, which reset() clears.
        first = list(self.feed(fx.forza_packet(is_race_on=1, smash_veldiff=50.0)))
        self.bridge.reset()
        held = self.feed(*[fx.forza_packet(is_race_on=1, smash_veldiff=50.0)
                           for _ in range(20)])
        self.assertEqual(first, [("crash", ())])
        self.assertEqual(held, [], "kept re-firing while the value stayed high")

    def test_an_impossible_delta_is_a_misread_not_a_crash(self):
        self.start_race()
        self.assertEqual(
            self.feed(fx.forza_packet(is_race_on=1, smash_veldiff=5000.0)), [])

    def test_an_oversized_packet_is_not_treated_as_fh6(self):
        """Beyond the known window it's a layout we don't know; don't guess."""
        self.start_race()
        self.assertEqual(
            self.feed(fx.forza_packet(is_race_on=1, smash_veldiff=20.0, size=600)),
            [])

    def test_no_crash_when_race_off(self):
        # Impact field only checked while the race is on.
        self.assertEqual(
            self.feed(fx.forza_packet(is_race_on=0, smash_veldiff=20.0)), [])

    # ── Gating / robustness ──────────────────────────────────────────────────
    def test_disabled_crash_does_not_fire(self):
        self.bridge.enabled_events = frozenset({"lights_out", "neutral"})  # crash off
        self.start_race()
        self.assertEqual(
            self.feed(fx.forza_packet(is_race_on=1, smash_veldiff=20.0)), [])

    def test_undersized_packet_ignored(self):
        self.assertEqual(self.feed(b"\x00" * 100), [])


class Horizon5AndMotorsportTests(unittest.TestCase):
    """The 311-byte Car Dash that Horizon 5 and Motorsport actually emit.

    Race start/end come from the Sled section, which is byte-identical across
    every Forza title, so they work here. Crash does not: offset 236 is
    PositionY in this layout, not a collision delta, and reading it as one is
    exactly the mistake to avoid.
    """

    def setUp(self):
        self.bridge = RecordingForzaBridge()

    def feed(self, *packets):
        for p in packets:
            self.bridge._handle_forza(p)
        return self.bridge.dispatches

    def test_race_start_works_on_a_car_dash_packet(self):
        self.assertEqual(self.feed(fx.forza_packet(is_race_on=1, size=311)),
                         [("lights_out", ())])

    def test_race_end_works_on_a_car_dash_packet(self):
        self.feed(fx.forza_packet(is_race_on=1, size=311))
        self.bridge.reset()
        self.assertEqual(self.feed(fx.forza_packet(is_race_on=0, size=311)),
                         [("neutral", ())])

    def test_a_car_high_above_sea_level_is_not_a_crash(self):
        """PositionY sits at 236. A hill is not an impact."""
        self.feed(fx.forza_packet(is_race_on=1, size=311))
        self.bridge.reset()
        self.assertEqual(
            self.feed(*[fx.forza_packet(is_race_on=1, position_y=y, size=311)
                        for y in (0.0, 12.0, 40.0, 150.0, 400.0)]),
            [], "a position coordinate was mistaken for a collision")

    def test_the_layout_is_named_for_the_user(self):
        describe = self.bridge._describe_layout
        self.assertIn("Sled", describe(232))
        self.assertIn("Horizon 5", describe(311))
        self.assertIn("Horizon 6", describe(339))
        self.assertIn("report", describe(600))


if __name__ == "__main__":
    unittest.main(verbosity=2)
