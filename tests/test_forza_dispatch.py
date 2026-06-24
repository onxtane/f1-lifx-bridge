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


if __name__ == "__main__":
    unittest.main(verbosity=2)
