"""The in-app effect replays must fire the effects they claim to.

A replay that sends a packet the parser ignores is worse than no replay: you'd
sit watching dark lights, unable to tell whether the tool or the effect was
broken. So these feed each replay's real packets through the real dispatcher and
assert what actually fires.

Also covers the two things the in-app version added over the CLI scripts: a
replay runs on a background thread, so it must stop promptly when asked, and the
UI builds its rows from the registry, so the registry has to stay coherent.
"""
import unittest

from tests import harness  # noqa: F401  — sets sys.path for the app modules
from tests.harness import RecordingF1Bridge  # noqa: E402
from bridge_core import (  # noqa: E402
    parse_header, parse_player_car_telemetry, parse_session_sector_flags,
)
import replay  # noqa: E402

HEADER_SIZE = 29  # F1 25 header


class _Capture(replay.Context):
    """A Context that collects packets instead of sending them, and never waits.

    Skips the socket (nothing to send to in a test) and the sleeps (a real
    replay runs ~30s), while keeping the stop check so interruption still works.
    """

    def __init__(self, stop=lambda: False):
        self.packets = []
        self.logs = []
        self.host, self.port = "test", 0
        self.log = self.logs.append
        self._should_stop = stop
        self._sock = None

    def send(self, packet, times=3, gap=0.05):
        self.check()
        self.packets.append(packet)

    def sleep(self, seconds):
        self.check()

    def close(self):
        pass


def _dispatch(packets):
    """Feed packets through the real F1 parser; return the effects that fired."""
    bridge = RecordingF1Bridge()
    for packet in packets:
        bridge.handle_packet(packet)
    return [effect for effect, _args in bridge.dispatches]


class F1EffectsReplayTests(unittest.TestCase):
    def setUp(self):
        self.ctx = _Capture()
        replay.run_f1_effects(self.ctx)
        self.fired = _dispatch(self.ctx.packets)

    def test_every_advertised_effect_actually_fires(self):
        """The registry promises these by name; the parser has to agree."""
        for key, label, _fn in replay.F1_EFFECTS:
            with self.subTest(effect=label):
                self.assertIn(key, self.fired)

    def test_start_lights_builds_up_one_at_a_time(self):
        bridge = RecordingF1Bridge()
        for packet in self.ctx.packets:
            bridge.handle_packet(packet)
        counts = [args[0] for effect, args in bridge.dispatches
                  if effect == "start_lights"]
        self.assertEqual(counts, [1, 2, 3, 4, 5])

    def test_effects_fire_in_race_order(self):
        """Lights out before the chequered flag, or the sequence tells a lie."""
        self.assertLess(self.fired.index("lights_out"),
                        self.fired.index("chequered_flag"))

    def test_only_the_requested_effect_runs_when_filtered(self):
        ctx = _Capture()
        replay.run_f1_effects(ctx, only=["red_flag"])
        self.assertEqual(_dispatch(ctx.packets), ["red_flag"])


class RpmMeterReplayTests(unittest.TestCase):
    def setUp(self):
        self.ctx = _Capture()
        replay.run_rpm_meter(self.ctx, speed=20)   # same shape, fewer frames

    def _rev_levels(self):
        levels = []
        for packet in self.ctx.packets:
            parsed = parse_player_car_telemetry(packet, parse_header(packet))
            if parsed:
                levels.append(parsed[0])
        return levels

    def test_the_sweep_reaches_the_redline_and_returns_to_idle(self):
        levels = self._rev_levels()
        self.assertTrue(levels, "no telemetry parsed out of the RPM replay")
        self.assertEqual(max(levels), 100, "never reached the redline")
        self.assertLessEqual(levels[0], replay.IDLE_PCT, "didn't start at idle")
        self.assertLessEqual(levels[-1], replay.IDLE_PCT, "didn't settle back to idle")

    def test_every_gear_change_drops_the_revs_to_that_gear_s_base(self):
        """A sweep that only ever climbs isn't a gearbox — it's a ramp."""
        levels = self._rev_levels()
        for gear, base_pct, _rpm, _secs in replay.UPSHIFTS:
            with self.subTest(gear=gear):
                self.assertIn(base_pct, levels)


class SectorStatusReplayTests(unittest.TestCase):
    def test_every_step_produces_a_packet_the_parser_accepts(self):
        ctx = _Capture()
        replay.run_sector_status(ctx)
        parsed = [parse_session_sector_flags(p, HEADER_SIZE) for p in ctx.packets]
        # Two of the nine steps are Event packets (lights out, red flag), which
        # aren't Session packets and so yield no sector flags.
        self.assertEqual(sum(1 for s in parsed if s is not None),
                         len(replay.SECTOR_SEQUENCE) - 2)

    def test_a_yellow_lands_in_the_sector_its_step_names(self):
        """The labels tell the user which sector to watch; they must be true."""
        ctx = _Capture()
        replay.run_sector_status(ctx)
        by_label = dict(zip([label for label, _ in replay.SECTOR_SEQUENCE],
                            [parse_session_sector_flags(p, HEADER_SIZE)
                             for p in ctx.packets]))
        self.assertEqual(by_label["Yellow in SECTOR 1"],
                         [replay.YELLOW, replay.GREEN, replay.GREEN])
        self.assertEqual(by_label["Yellow in SECTOR 2"],
                         [replay.GREEN, replay.YELLOW, replay.GREEN])
        self.assertEqual(by_label["Yellow in SECTOR 3"],
                         [replay.GREEN, replay.GREEN, replay.YELLOW])

    def test_the_race_is_started_before_flags_are_shown(self):
        """Flag flashes are gated on the race having started."""
        ctx = _Capture()
        replay.run_sector_status(ctx)
        self.assertIn("lights_out", _dispatch(ctx.packets))


class StopTests(unittest.TestCase):
    """A replay runs ~30s on a thread; Stop has to land inside a step, not after."""

    def test_stopping_ends_the_replay_almost_immediately(self):
        ctx = _Capture(stop=lambda: True)
        with self.assertRaises(replay.Stopped):
            replay.run_f1_effects(ctx)
        self.assertEqual(ctx.packets, [], "kept sending after being told to stop")

    def test_stop_partway_through_keeps_what_it_already_sent(self):
        sent = []

        class _StopAfterThree(_Capture):
            def send(self, packet, times=3, gap=0.05):
                self.check()
                sent.append(packet)

        ctx = _StopAfterThree(stop=lambda: len(sent) >= 3)
        with self.assertRaises(replay.Stopped):
            replay.run_f1_effects(ctx)
        self.assertEqual(len(sent), 3)

    def test_run_reports_a_stop_rather_than_raising(self):
        """Stopping is a normal outcome; the UI shouldn't see an exception."""
        calls = []
        completed = replay.run("sector_status", "127.0.0.1", 20777,
                               calls.append, should_stop=lambda: True)
        self.assertFalse(completed)
        self.assertTrue(any("stopped" in line for line in calls))


class RegistryTests(unittest.TestCase):
    """The UI builds its rows from this, so it has to stay coherent."""

    def test_the_three_replays_are_registered(self):
        self.assertEqual([r.key for r in replay.REPLAYS],
                         ["f1_effects", "rpm_meter", "sector_status"])

    def test_every_replay_is_runnable_and_described(self):
        for r in replay.REPLAYS:
            with self.subTest(replay=r.key):
                self.assertTrue(callable(r.fn))
                self.assertTrue(r.label and r.blurb)
                self.assertGreater(r.seconds, 0)
                self.assertIs(replay.BY_KEY[r.key], r)


if __name__ == "__main__":
    unittest.main()
