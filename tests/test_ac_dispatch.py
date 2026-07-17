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


class FlagTests(unittest.TestCase):
    """AC exposes a real flag enum — the thing GridGlow is built around."""

    def setUp(self):
        self.bridge = RecordingACBridge()

    def test_each_flag_fires_its_effect(self):
        for flag, effect in ((AC_YELLOW_FLAG, "yellow_flag"),
                             (AC_BLUE_FLAG, "blue_flag"),
                             (AC_WHITE_FLAG, "white_warning"),
                             (AC_BLACK_FLAG, "black_flag"),
                             (AC_CHECKERED_FLAG, "chequered_flag"),
                             (AC_PENALTY_FLAG, "white_warning")):
            bridge = RecordingACBridge()
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


class RaceStartTests(unittest.TestCase):
    def test_first_lap_of_a_race_fires_lights_out(self):
        """AC has no start-light sequence to read, so the race's first lap
        transition is the closest honest signal."""
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(session=AC_RACE, completed_laps=0))
        bridge.reset()
        fired = [e for e, _a in _feed(bridge, graphics=fx.ac_graphics(
            session=AC_RACE, completed_laps=1))]
        self.assertIn("lights_out", fired)

    def test_later_laps_do_not_re_fire(self):
        bridge = RecordingACBridge()
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=3))
        bridge.reset()
        _feed(bridge, graphics=fx.ac_graphics(completed_laps=4))
        self.assertEqual(bridge.dispatches, [])


class RpmTests(unittest.TestCase):
    """AC gives raw revs + a ceiling; F1 gives a percent. Both go through the
    same dispatcher, so the throttle behaves identically."""

    def setUp(self):
        self.bridge = RecordingACBridge(max_rpm=8000)
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
