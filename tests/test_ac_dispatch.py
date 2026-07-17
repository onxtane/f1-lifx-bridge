"""Assetto Corsa shared memory → effect dispatch (#49).

AC is the first title GridGlow reads from memory rather than a socket, so two
things are worth proving separately:

  - The reader attaches only to a map the game already made, and knows when the
    game isn't there. `mmap(-1, tagname=...)` would silently *create* the map,
    leaving GridGlow reading its own zeroes and unable to tell a stopped game
    from a stationary car.
  - Dispatch keys off `flag`, `status` and the revs — tested the same way every
    other title is, by feeding state in and asserting what fires.
"""
import ctypes
import mmap
import unittest

from tests import harness  # noqa: F401  — sets sys.path for the app modules
from tests import fixtures as fx  # noqa: E402
from tests.harness import RecordingACBridge  # noqa: E402
from ac_bridge import (  # noqa: E402
    ACGraphics, ACPhysics, AC_LIVE, AC_OFF, AC_PAUSE, AC_REPLAY, AC_RACE,
    AC_NO_FLAG, AC_BLUE_FLAG, AC_YELLOW_FLAG, AC_BLACK_FLAG, AC_WHITE_FLAG,
    AC_CHECKERED_FLAG, AC_PENALTY_FLAG,
)
from shared_memory import SharedMemoryMap  # noqa: E402


class StructLayoutTests(unittest.TestCase):
    """The layouts are documentation-derived, so pin what we can check locally."""

    def test_flag_sits_where_the_compiler_puts_it_not_where_addition_says(self):
        """tyreCompound[33] ends at 242; the next float aligns to 244, pushing
        flag to 268. Hand-computed offsets say 266 and read carCoordinates."""
        self.assertEqual(ACGraphics.flag.offset, 268)
        self.assertEqual(ACGraphics.replayTimeMultiplier.offset, 244)

    def test_physics_fields_need_no_padding(self):
        self.assertEqual(ACPhysics.rpms.offset, 20)
        self.assertEqual(ACPhysics.speedKmh.offset, 28)

    def test_structs_are_a_prefix_of_the_real_maps(self):
        """We declare only as deep as we read; mapping a prefix is the point."""
        self.assertEqual(ctypes.sizeof(ACPhysics), 56)
        self.assertEqual(ctypes.sizeof(ACGraphics), 288)


class SharedMemoryReaderTests(unittest.TestCase):
    """The reader must never invent a map the game hasn't made."""

    TAG = "Local\\gridglow_test_map"

    def test_absent_map_reports_not_attached(self):
        m = SharedMemoryMap("Local\\gridglow_definitely_absent", 64)
        self.addCleanup(m.close)
        self.assertFalse(m.open(), "opened a map that does not exist")
        self.assertFalse(m.attached)
        self.assertIsNone(m.read())

    def test_reads_a_map_another_process_made(self):
        # Stand in for the game: create the map and hold it open.
        writer = mmap.mmap(-1, 64, tagname=self.TAG)
        self.addCleanup(writer.close)
        writer.write(b"\xAA" * 64)

        m = SharedMemoryMap(self.TAG, 64)
        self.addCleanup(m.close)
        self.assertTrue(m.open(), "could not attach to an existing map")
        self.assertTrue(m.attached)
        self.assertEqual(m.read(), b"\xAA" * 64)

    def test_opening_an_absent_map_does_not_create_it(self):
        """The whole reason for OpenFileMappingW over mmap(-1, tagname=...).

        If open() created the map, GridGlow would invent acpmf_physics before
        the game did, then read its own zeroes forever.
        """
        tag = "Local\\gridglow_must_not_be_created"
        a = SharedMemoryMap(tag, 64)
        self.addCleanup(a.close)
        a.open()
        b = SharedMemoryMap(tag, 64)
        self.addCleanup(b.close)
        self.assertFalse(b.open(), "the first open() created the map")


def _feed(bridge, physics=None, graphics=None):
    bridge._handle_ac(physics or fx.ac_physics(), graphics or fx.ac_graphics())
    return bridge.dispatches


def _primed(bridge=None):
    """A bridge past its first sample, with dispatches cleared.

    The first sample legitimately fires neutral — flag goes from "unknown" to
    AC_NO_FLAG, which is the bridge setting your lights to idle on attach — and
    speed needs a baseline before a drop can be measured. Priming means a test
    sees only the thing it's testing.
    """
    bridge = bridge or RecordingACBridge()
    _feed(bridge, physics=fx.ac_physics(speed_kmh=200.0))
    bridge.reset()
    return bridge


class FlagTests(unittest.TestCase):
    """AC exposes a real flag enum — the thing GridGlow is built around."""

    def setUp(self):
        self.bridge = _primed()

    def test_each_flag_fires_its_effect(self):
        for flag, effect in ((AC_YELLOW_FLAG, "yellow_flag"),
                             (AC_BLUE_FLAG, "blue_flag"),
                             (AC_WHITE_FLAG, "white_warning"),
                             (AC_BLACK_FLAG, "black_flag"),
                             (AC_CHECKERED_FLAG, "chequered_flag"),
                             (AC_PENALTY_FLAG, "white_warning")):
            bridge = _primed()
            with self.subTest(flag=flag):
                fired = [e for e, _a in _feed(bridge, graphics=fx.ac_graphics(flag=flag))]
                self.assertIn(effect, fired)

    def test_a_held_flag_fires_once_not_every_sample(self):
        """flag holds its value every frame; only the edge is an event."""
        g = fx.ac_graphics(flag=AC_YELLOW_FLAG)
        first = list(_feed(self.bridge, graphics=g))
        self.bridge.reset()
        for _ in range(30):
            _feed(self.bridge, graphics=g)
        self.assertEqual([e for e, _a in first], ["yellow_flag"])
        self.assertEqual(self.bridge.dispatches, [], "re-fired while the flag was held")

    def test_clearing_a_flag_returns_to_neutral(self):
        _feed(self.bridge, graphics=fx.ac_graphics(flag=AC_YELLOW_FLAG))
        self.bridge.reset()
        fired = [e for e, _a in _feed(self.bridge, graphics=fx.ac_graphics(flag=AC_NO_FLAG))]
        self.assertEqual(fired, ["neutral"])

    def test_a_disabled_effect_does_not_fire(self):
        self.bridge.enabled_events = frozenset({"neutral"})
        fired = [e for e, _a in _feed(self.bridge, graphics=fx.ac_graphics(flag=AC_YELLOW_FLAG))]
        self.assertEqual(fired, [])


class StatusGateTests(unittest.TestCase):
    """The maps stay populated in menus, replays and pause. Without the gate a
    replay would drive the lights."""

    def setUp(self):
        self.bridge = RecordingACBridge()

    def test_nothing_fires_unless_the_session_is_live(self):
        for status in (AC_OFF, AC_REPLAY, AC_PAUSE):
            bridge = RecordingACBridge()
            with self.subTest(status=status):
                fired = _feed(bridge, graphics=fx.ac_graphics(
                    status=status, flag=AC_YELLOW_FLAG))
                self.assertEqual(fired, [])

    def test_leaving_a_live_session_returns_to_idle(self):
        _feed(self.bridge, graphics=fx.ac_graphics(status=AC_LIVE))
        self.bridge.reset()
        fired = [e for e, _a in _feed(self.bridge, graphics=fx.ac_graphics(status=AC_OFF))]
        self.assertEqual(fired, ["neutral"])

    def test_revs_do_not_drive_the_meter_during_a_replay(self):
        bridge = RecordingACBridge(max_rpm=8000)
        bridge.enabled_events = frozenset({"rpm_meter"})
        fired = _feed(bridge,
                      physics=fx.ac_physics(rpms=7800),
                      graphics=fx.ac_graphics(status=AC_REPLAY))
        self.assertEqual(fired, [])


class LayoutTimingTests(unittest.TestCase):
    """maxRpm and the layout line must be read on the first LIVE sample.

    Found on ACC: attaching from the menu read the static map while empty, so
    maxRpm cached as 0 and the RPM meter was dead for the whole session, and
    the layout line logged useless zeros. Latent on AC too — it just happened
    to attach in a session. Base-class fix, so tested here.
    """

    def _bridge_reading_static(self, max_rpm):
        from ac_bridge import ACStatic
        bridge = RecordingACBridge()
        bridge._ac_logged_layout = False        # let the layout fire
        bridge._ac_max_rpm = 0
        self.logs = []
        bridge.log = self.logs.append
        static = ACStatic(maxRpm=max_rpm)
        bridge._static.read = lambda: bytes(static)
        return bridge

    def test_a_menu_sample_does_not_read_static(self):
        bridge = self._bridge_reading_static(7800)
        _feed(bridge, graphics=fx.ac_graphics(status=AC_OFF))
        self.assertEqual(bridge._ac_max_rpm, 0, "read static before the session was live")
        self.assertFalse(any("layout" in l for l in self.logs))

    def test_the_first_live_sample_reads_maxrpm(self):
        bridge = self._bridge_reading_static(7800)
        _feed(bridge, graphics=fx.ac_graphics(status=AC_OFF))     # menu
        _feed(bridge, graphics=fx.ac_graphics(status=AC_LIVE))    # session loads
        self.assertEqual(bridge._ac_max_rpm, 7800)
        self.assertTrue(any("layout" in l for l in self.logs))

    def test_the_rpm_meter_works_after_a_menu_then_live_attach(self):
        """The end-to-end symptom: revs must drive the meter once live."""
        bridge = self._bridge_reading_static(8000)
        bridge.enabled_events = frozenset({"rpm_meter"})
        _feed(bridge, graphics=fx.ac_graphics(status=AC_OFF))
        _feed(bridge, graphics=fx.ac_graphics(status=AC_LIVE))   # seeds
        bridge.reset()
        fired = _feed(bridge, physics=fx.ac_physics(rpms=4000),
                      graphics=fx.ac_graphics(status=AC_LIVE))
        self.assertEqual(fired, [("rpm_meter", (50,))])


class RaceStartTests(unittest.TestCase):
    """Regression: this fired on the first *completed* lap — the end of lap
    one, a whole lap late, and it read as firing every time you crossed the
    line. iCurrentTime sits at 0 through the grid countdown and starts the
    instant you're released, which is the actual moment.
    """

    def test_the_lap_timer_starting_is_the_race_start(self):
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(session=AC_RACE, completed_laps=0,
                                              current_time_ms=0))   # countdown
        bridge.reset()
        fired = [e for e, _a in _feed(bridge, graphics=fx.ac_graphics(
            session=AC_RACE, completed_laps=0, current_time_ms=120))]
        self.assertEqual(fired, ["lights_out"])

    def test_it_does_not_wait_for_the_first_lap_to_complete(self):
        """The bug: nothing should be pending by the time you cross the line."""
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=0, current_time_ms=0))
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=0, current_time_ms=500))
        bridge.reset()
        # Crossing the line to start lap 2 must not fire a race start.
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=1, current_time_ms=100))
        self.assertEqual(bridge.dispatches, [])

    def test_it_fires_once_not_every_sample_of_lap_one(self):
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=0, current_time_ms=0))
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=0, current_time_ms=100))
        bridge.reset()
        for t in (200, 300, 400, 500):
            _feed(bridge, graphics=fx.ac_graphics(completed_laps=0, current_time_ms=t))
        self.assertEqual(bridge.dispatches, [])

    def test_crossing_the_line_on_later_laps_never_fires(self):
        bridge = _primed()
        for laps in (1, 2, 3, 4):
            _feed(bridge, graphics=fx.ac_graphics(completed_laps=laps,
                                                  current_time_ms=100))
        self.assertEqual([e for e, _a in bridge.dispatches if e == "lights_out"], [])

    def test_a_new_race_re_arms_the_start(self):
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=0, current_time_ms=0))
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=0, current_time_ms=100))
        # Back to the grid for a new race.
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=0, current_time_ms=0))
        bridge.reset()
        fired = [e for e, _a in _feed(bridge, graphics=fx.ac_graphics(
            completed_laps=0, current_time_ms=90))]
        self.assertEqual(fired, ["lights_out"])

    def test_a_practice_session_is_not_a_race_start(self):
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(session=0, completed_laps=0,
                                              current_time_ms=0))
        bridge.reset()
        _feed(bridge, graphics=fx.ac_graphics(session=0, completed_laps=0,
                                              current_time_ms=500))
        self.assertEqual([e for e, _a in bridge.dispatches if e == "lights_out"], [])


class JoinInProgressTests(unittest.TestCase):
    """Regression: attaching mid-session announced whatever was already there.

    The real log showed a personal best from a previous session and a chequered
    flag from a race that finished before GridGlow started — both fired the
    instant it attached.
    """

    def test_a_stale_best_lap_is_adopted_not_announced(self):
        bridge = RecordingACBridge()
        fired = _feed(bridge, graphics=fx.ac_graphics(best_time_ms=89678))
        self.assertEqual(fired, [], "announced a lap set before we were watching")

    def test_a_stale_chequered_flag_is_adopted_not_announced(self):
        bridge = RecordingACBridge()
        fired = _feed(bridge, graphics=fx.ac_graphics(flag=AC_CHECKERED_FLAG))
        self.assertEqual(fired, [])

    def test_joining_a_race_already_underway_does_not_fire_a_start(self):
        bridge = RecordingACBridge()
        fired = _feed(bridge, graphics=fx.ac_graphics(session=AC_RACE,
                                                      completed_laps=0,
                                                      current_time_ms=45000))
        self.assertEqual(fired, [])

    def test_the_adopted_best_still_gates_later_laps(self):
        """Seeding must set the baseline, not just skip the first sample."""
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(best_time_ms=89678))
        bridge.reset()
        _feed(bridge, graphics=fx.ac_graphics(best_time_ms=91000))   # slower
        self.assertEqual(bridge.dispatches, [])
        fired = [e for e, _a in _feed(bridge, graphics=fx.ac_graphics(best_time_ms=87149))]
        self.assertEqual(fired, ["fastest_lap"])

    def test_a_flag_change_after_joining_still_fires(self):
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(flag=AC_CHECKERED_FLAG))
        bridge.reset()
        fired = [e for e, _a in _feed(bridge, graphics=fx.ac_graphics(flag=AC_NO_FLAG))]
        self.assertEqual(fired, ["neutral"])

    def test_reattaching_forgets_the_old_session(self):
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(best_time_ms=87149))
        bridge._ac_detach()          # game closed
        bridge.reset()
        # New session, slower car: the old best must not suppress it.
        fired = _feed(bridge, graphics=fx.ac_graphics(best_time_ms=95000))
        self.assertEqual(fired, [], "should adopt, not announce")
        bridge.reset()
        fired = [e for e, _a in _feed(bridge, graphics=fx.ac_graphics(best_time_ms=94000))]
        self.assertEqual(fired, ["fastest_lap"])


class FastestLapTests(unittest.TestCase):
    """iBestTime improving is AC's equivalent of F1's FTLP."""

    def setUp(self):
        self.bridge = _primed()

    def test_a_first_valid_lap_is_a_personal_best(self):
        fired = [e for e, _a in _feed(self.bridge,
                                      graphics=fx.ac_graphics(best_time_ms=88500))]
        self.assertEqual(fired, ["fastest_lap"])

    def test_beating_your_best_fires_again(self):
        _feed(self.bridge, graphics=fx.ac_graphics(best_time_ms=88500))
        self.bridge.reset()
        fired = [e for e, _a in _feed(self.bridge,
                                      graphics=fx.ac_graphics(best_time_ms=87200))]
        self.assertEqual(fired, ["fastest_lap"])

    def test_a_slower_lap_is_not_a_best(self):
        _feed(self.bridge, graphics=fx.ac_graphics(best_time_ms=88500))
        self.bridge.reset()
        _feed(self.bridge, graphics=fx.ac_graphics(best_time_ms=91000))
        self.assertEqual(self.bridge.dispatches, [])

    def test_holding_the_same_best_does_not_re_fire(self):
        """iBestTime is in every sample, not an event."""
        _feed(self.bridge, graphics=fx.ac_graphics(best_time_ms=88500))
        self.bridge.reset()
        for _ in range(30):
            _feed(self.bridge, graphics=fx.ac_graphics(best_time_ms=88500))
        self.assertEqual(self.bridge.dispatches, [])

    def test_the_no_lap_sentinel_is_not_a_lap_time(self):
        """AC parks iBestTime on a sentinel before you set one. 0 and a huge
        int are both reported in the wild, so neither may count."""
        for sentinel in (0, 2147483647, 99999999):
            bridge = _primed()
            with self.subTest(sentinel=sentinel):
                self.assertEqual(
                    _feed(bridge, graphics=fx.ac_graphics(best_time_ms=sentinel)), [])


class CrashTests(unittest.TestCase):
    """A G spike *and* speed genuinely lost — the same two-signal test DiRT
    Rally uses, because G alone fires on kerbs and hard cornering."""

    def setUp(self):
        self.bridge = _primed()          # 200 km/h baseline to fall from

    def test_a_hard_impact_fires_crash(self):
        fired = [e for e, _a in _feed(self.bridge, physics=fx.ac_physics(
            speed_kmh=150.0, g_lat=4.0, g_lon=3.0))]
        self.assertEqual(fired, ["crash"])

    def test_hard_cornering_is_not_a_crash(self):
        """High G while carrying speed is just a fast corner."""
        self.assertEqual(_feed(self.bridge, physics=fx.ac_physics(
            speed_kmh=199.0, g_lat=4.5, g_lon=0.0)), [])

    def test_braking_is_not_a_crash(self):
        """Losing a lot of speed under braking, without the G spike."""
        self.assertEqual(_feed(self.bridge, physics=fx.ac_physics(
            speed_kmh=120.0, g_lat=0.2, g_lon=1.5)), [])

    def test_a_kerb_is_not_a_crash(self):
        """Vertical G is bumps and kerbs — it must not count toward the spike."""
        p = fx.ac_physics(speed_kmh=150.0)
        p.accG[1] = 9.0                      # a big vertical hit
        self.assertEqual(_feed(self.bridge, physics=p), [])

    def test_crashes_are_rate_limited(self):
        """One impact spans many samples at 60 Hz."""
        _feed(self.bridge, physics=fx.ac_physics(speed_kmh=150.0, g_lat=4.0, g_lon=3.0))
        self.bridge.reset()
        for speed in (140.0, 120.0, 100.0):
            _feed(self.bridge, physics=fx.ac_physics(
                speed_kmh=speed, g_lat=4.0, g_lon=3.0))
        self.assertEqual(self.bridge.dispatches, [])


class RpmTests(unittest.TestCase):
    """AC gives raw revs + a ceiling; F1 gives a percent. Both go through the
    same dispatcher, so the throttle behaves identically."""

    def setUp(self):
        self.bridge = _primed(RecordingACBridge(max_rpm=8000))
        self.bridge.enabled_events = frozenset({"rpm_meter"})

    def test_revs_drive_the_meter_as_a_percent_of_max(self):
        fired = _feed(self.bridge, physics=fx.ac_physics(rpms=4000))
        self.assertEqual(fired, [("rpm_meter", (50,))])

    def test_the_limiter_hands_off_to_the_redline_blink(self):
        fired = [e for e, _a in _feed(self.bridge, physics=fx.ac_physics(rpms=8000))]
        self.assertEqual(fired, ["rpm_redline"])

    def test_small_rev_changes_do_not_repaint(self):
        """The throttle is the reason 60 Hz polling doesn't flood the LAN."""
        _feed(self.bridge, physics=fx.ac_physics(rpms=4000))
        self.bridge.reset()
        for rpms in (4010, 4020, 4030):
            _feed(self.bridge, physics=fx.ac_physics(rpms=rpms))
        self.assertEqual(self.bridge.dispatches, [])

    def test_no_meter_without_a_known_ceiling(self):
        """maxRpm comes from acpmf_static; dividing by zero isn't a meter."""
        bridge = RecordingACBridge(max_rpm=0)
        bridge.enabled_events = frozenset({"rpm_meter"})
        self.assertEqual(_feed(bridge, physics=fx.ac_physics(rpms=7000)), [])


if __name__ == "__main__":
    unittest.main()
