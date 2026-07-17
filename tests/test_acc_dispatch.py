"""Assetto Corsa Competizione flag dispatch (#79).

ACC inherits AC's entire dispatch — race start, personal best, crash, RPM,
the status gate, the priming that stops a stale session firing on attach — and
those are covered by test_ac_dispatch. What's ACC's own, and all this file
tests, is the flag handling: ACC exposes globalYellow / globalWhite /
globalChequered / globalRed as dedicated fields (plus per-sector yellows and a
real red flag, which AC has no equivalent for) rather than AC's single enum.

These drive synthetic structs, so they prove the *logic* regardless of whether
the real-game struct offsets are right — that second question needs the game
and the [ACC] layout log, and is called out in #79.
"""
import ctypes
import unittest

from tests import harness  # noqa: F401  — sets sys.path for the app modules
from tests import fixtures as fx  # noqa: E402
from tests.harness import RecordingACCBridge  # noqa: E402
from ac_bridge import ACGraphics, AC_BLUE_FLAG  # noqa: E402
from acc_bridge import ACCGraphics  # noqa: E402


def _feed(bridge, physics=None, graphics=None):
    bridge._handle_ac(physics or fx.ac_physics(), graphics or fx.acc_graphics())
    return bridge.dispatches


def _primed(bridge=None):
    """Past the first (state-adopting) sample, dispatches cleared."""
    bridge = bridge or RecordingACCBridge()
    _feed(bridge, physics=fx.ac_physics(speed_kmh=200.0))
    bridge.reset()
    return bridge


class StructLayoutTests(unittest.TestCase):
    """The struct is documentation-derived and unverifiable without the game;
    pin the two things that ARE checkable here."""

    def test_the_head_is_byte_identical_to_ac(self):
        """The inherited dispatch reads only the head, so it must match AC
        exactly — status, laps, lap times all at the same offsets."""
        for f in ("packetId", "status", "session", "completedLaps",
                  "iCurrentTime", "iLastTime", "iBestTime",
                  "normalizedCarPosition"):
            with self.subTest(field=f):
                self.assertEqual(getattr(ACCGraphics, f).offset,
                                 getattr(ACGraphics, f).offset)

    def test_the_global_flags_sit_past_the_car_matrix(self):
        """Sanity that the 60-car block is accounted for: globals are ~1500
        bytes in, not where AC's single flag is (268)."""
        self.assertGreater(ACCGraphics.globalYellow.offset, 1400)
        self.assertGreater(ACCGraphics.globalRed.offset, ACCGraphics.globalYellow.offset)


class GlobalFlagTests(unittest.TestCase):
    def setUp(self):
        self.bridge = _primed()

    def test_global_yellow_fires_yellow(self):
        fired = [e for e, _a in _feed(self.bridge, graphics=fx.acc_graphics(g_yellow=1))]
        self.assertEqual(fired, ["yellow_flag"])

    def test_global_red_fires_red(self):
        """ACC has a red flag; AC has none. This is the case AC can't do."""
        fired = [e for e, _a in _feed(self.bridge, graphics=fx.acc_graphics(g_red=1))]
        self.assertEqual(fired, ["red_flag"])

    def test_global_white_fires_white_warning(self):
        fired = [e for e, _a in _feed(self.bridge, graphics=fx.acc_graphics(g_white=1))]
        self.assertEqual(fired, ["white_warning"])

    def test_global_chequered_fires_chequered(self):
        fired = [e for e, _a in _feed(self.bridge, graphics=fx.acc_graphics(g_chequered=1))]
        self.assertEqual(fired, ["chequered_flag"])

    def test_a_per_sector_yellow_counts_as_yellow(self):
        """globalYellow1/2/3 are per-sector — any one is a yellow somewhere."""
        for sector in ("g_yellow1", "g_yellow2", "g_yellow3"):
            bridge = _primed()
            with self.subTest(sector=sector):
                fired = [e for e, _a in _feed(bridge, graphics=fx.acc_graphics(**{sector: 1}))]
                self.assertEqual(fired, ["yellow_flag"])

    def test_clearing_a_global_flag_returns_to_neutral(self):
        _feed(self.bridge, graphics=fx.acc_graphics(g_yellow=1))
        self.bridge.reset()
        fired = [e for e, _a in _feed(self.bridge, graphics=fx.acc_graphics())]
        self.assertEqual(fired, ["neutral"])

    def test_a_held_flag_fires_once(self):
        g = fx.acc_graphics(g_yellow=1)
        _feed(self.bridge, graphics=g)
        self.bridge.reset()
        for _ in range(20):
            _feed(self.bridge, graphics=g)
        self.assertEqual(self.bridge.dispatches, [])

    def test_red_outranks_a_simultaneous_yellow(self):
        """Several globals can be set at once; the most serious owns the strip."""
        fired = [e for e, _a in _feed(self.bridge,
                                      graphics=fx.acc_graphics(g_yellow=1, g_red=1))]
        self.assertEqual(fired, ["red_flag"])

    def test_a_disabled_flag_does_not_fire(self):
        self.bridge.enabled_events = frozenset({"neutral"})
        self.assertEqual(_feed(self.bridge, graphics=fx.acc_graphics(g_yellow=1)), [])


class BlueFlagTests(unittest.TestCase):
    """Blue is still per-car (the `flag` enum) — being lapped is about your car,
    not a session-wide state."""

    def setUp(self):
        self.bridge = _primed()

    def test_blue_flag_fires_from_the_per_car_enum(self):
        fired = [e for e, _a in _feed(self.bridge,
                                      graphics=fx.acc_graphics(flag=AC_BLUE_FLAG))]
        self.assertEqual(fired, ["blue_flag"])

    def test_blue_fires_once_while_held(self):
        g = fx.acc_graphics(flag=AC_BLUE_FLAG)
        _feed(self.bridge, graphics=g)
        self.bridge.reset()
        for _ in range(15):
            _feed(self.bridge, graphics=g)
        self.assertEqual(self.bridge.dispatches, [])

    def test_a_global_and_a_blue_can_fire_together(self):
        """A yellow somewhere and you being lapped are independent."""
        fired = [e for e, _a in _feed(self.bridge,
                                      graphics=fx.acc_graphics(g_yellow=1, flag=AC_BLUE_FLAG))]
        self.assertEqual(set(fired), {"yellow_flag", "blue_flag"})


class JoinInProgressTests(unittest.TestCase):
    """The AC priming fix must carry over: attaching mid-session adopts the
    global flags rather than announcing them."""

    def test_a_stale_global_flag_is_not_announced_on_attach(self):
        bridge = RecordingACCBridge()
        fired = _feed(bridge, graphics=fx.acc_graphics(g_yellow=1))
        self.assertEqual(fired, [], "announced a flag that was already flying")

    def test_a_stale_red_is_adopted_then_clearing_it_fires(self):
        bridge = RecordingACCBridge()
        _feed(bridge, graphics=fx.acc_graphics(g_red=1))     # adopt
        bridge.reset()
        fired = [e for e, _a in _feed(bridge, graphics=fx.acc_graphics())]
        self.assertEqual(fired, ["neutral"])


class InheritedFromACTests(unittest.TestCase):
    """Spot-check that the reused AC dispatch still works through ACC — same
    code, but proves the ACC struct feeds it correctly."""

    def test_race_start_still_works(self):
        bridge = RecordingACCBridge()
        _feed(bridge, graphics=fx.acc_graphics(completed_laps=0, current_time_ms=0))
        bridge.reset()
        fired = [e for e, _a in _feed(bridge, graphics=fx.acc_graphics(
            completed_laps=0, current_time_ms=90))]
        self.assertEqual(fired, ["lights_out"])

    def test_personal_best_still_works(self):
        bridge = _primed()
        fired = [e for e, _a in _feed(bridge, graphics=fx.acc_graphics(best_time_ms=95000))]
        self.assertEqual(fired, ["fastest_lap"])

    def test_rpm_still_works(self):
        bridge = _primed(RecordingACCBridge(max_rpm=8000))
        bridge.enabled_events = frozenset({"rpm_meter"})
        fired = _feed(bridge, physics=fx.ac_physics(rpms=4000))
        self.assertEqual(fired, [("rpm_meter", (50,))])

    def test_crash_is_disarmed_by_global_chequered(self):
        """AC checks flag==CHECKERED; ACC must check globalChequered instead."""
        bridge = _primed()
        # Chequered out, then AC's end-of-session car reset spikes the G.
        fired = _feed(bridge, physics=fx.ac_physics(speed_kmh=160.0, g_lat=20.0, g_lon=16.0),
                      graphics=fx.acc_graphics(g_chequered=1))
        self.assertNotIn("crash", [e for e, _a in fired])


if __name__ == "__main__":
    unittest.main()
