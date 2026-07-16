"""What the RPM meter actually paints, and onto which lights (#71 follow-up).

Two behaviours that used to be wrong:

  - The meter grabbed every multizone strip regardless of light assignment, so
    there was no way to keep one strip on flags while another ran the revs.
  - The redline flashed one solid colour. It flashes the whole gradient now —
    at the limiter the strip is full, so the blink is that same picture
    switching on and off.

These drive the real LocalLifxController against fake strips that record their
set_zone_color calls, so they assert the bytes that would go to hardware.
"""
import contextlib
import io
import unittest

from tests import harness  # noqa: F401  — sets sys.path for the app modules
from bridge_core import (  # noqa: E402
    LocalLifxController, MultiZoneLight, RPM_FILL_BRIGHTNESS,
    parse_rpm_gradient, sample_rpm_gradient,
)


class _FakeStrip(MultiZoneLight):
    """A multizone strip that records paints instead of sending UDP."""

    def __init__(self, label, zones=8):
        self.label = label
        self.zones = zones
        self.paints = []          # (zone_idx, color, duration, apply)

    def get_label(self):
        return self.label

    def set_zone_color(self, start, end, color, duration=0, rapid=False, apply=1):
        self.paints.append((start, list(color), duration, apply))

    def get_color_zones(self, start=0, end=255):
        return [None] * self.zones


class _FakeBulb:
    """A plain bulb — the meter must ignore it however it's assigned."""

    def __init__(self, label):
        self.label = label
        self.paints = []

    def get_label(self):
        return self.label


def _controller(lights, assignments=None):
    # Built dry so __init__ doesn't go looking for bulbs on the LAN, then
    # un-dried: dry_run also short-circuits the paint path, which is the thing
    # under test. The fake lights record the sends that would have gone out.
    with contextlib.redirect_stdout(io.StringIO()):     # it announces DRY_RUN
        ctrl = LocalLifxController(bulb_count=0, select_in_console=False,
                                   use_saved_groups=False, dry_run=True)
    ctrl.dry_run = False
    ctrl.lights = lights
    ctrl.light_assignments = assignments or {}
    # Zone counts normally come from discovery; fake strips report their own.
    ctrl.get_zone_count = lambda light: getattr(light, "zones", 0)
    ctrl.safe_label = lambda light: light.label
    return ctrl


class LightAssignmentTests(unittest.TestCase):
    """The meter has to respect Light Assignment like every other effect."""

    def setUp(self):
        self.a = _FakeStrip("Strip A")
        self.b = _FakeStrip("Strip B")

    def test_meter_only_paints_strips_assigned_to_it(self):
        ctrl = _controller([self.a, self.b],
                           {"Strip A": ["rpm_meter"], "Strip B": ["yellow_flag"]})
        ctrl.rpm_meter(50)
        self.assertTrue(self.a.paints, "assigned strip was not painted")
        self.assertEqual(self.b.paints, [], "unassigned strip was painted anyway")

    def test_no_assignments_means_every_strip_still_runs(self):
        """The default has to keep working for anyone who never configured it."""
        ctrl = _controller([self.a, self.b], {})
        ctrl.rpm_meter(50)
        self.assertTrue(self.a.paints)
        self.assertTrue(self.b.paints)

    def test_a_light_set_to_all_effects_runs_the_meter(self):
        ctrl = _controller([self.a, self.b], {"Strip A": None, "Strip B": ["red_flag"]})
        ctrl.rpm_meter(50)
        self.assertTrue(self.a.paints)
        self.assertEqual(self.b.paints, [])

    def test_plain_bulbs_are_never_painted_by_the_meter(self):
        bulb = _FakeBulb("Lamp")
        ctrl = _controller([self.a, bulb], {"Lamp": ["rpm_meter"]})
        ctrl.rpm_meter(50)
        self.assertTrue(self.a.paints)
        self.assertEqual(bulb.paints, [])

    def test_assigning_the_meter_nowhere_warns_once_and_paints_nothing(self):
        logs = []
        ctrl = _controller([self.a], {"Strip A": ["yellow_flag"]})
        ctrl.log_callback = logs.append
        ctrl.rpm_meter(50)
        ctrl.rpm_meter(60)
        self.assertEqual(self.a.paints, [])
        self.assertEqual(len(logs), 1, "the no-strip notice should not repeat")
        self.assertIn("Light Assignment", logs[0])


class FillTests(unittest.TestCase):
    def test_fill_lights_zones_in_proportion_to_revs(self):
        strip = _FakeStrip("S", zones=10)
        ctrl = _controller([strip])
        ctrl.rpm_meter(50)
        grad = parse_rpm_gradient(("#00ff00", "#ff0000"))
        dark = [0, 0, ctrl._scale_brightness(150), 3500]
        lit = [p for p in strip.paints if p[1] != dark]
        self.assertEqual(len(lit), 5)

    def test_fill_colours_follow_the_gradient(self):
        strip = _FakeStrip("S", zones=8)
        ctrl = _controller([strip])
        ctrl.rpm_gradient = parse_rpm_gradient(("#0000ff", "#ffffff"))
        ctrl.rpm_meter(100)
        hues = {p[1][0] for p in strip.paints}
        self.assertEqual(hues, {43690}, "a blue->white ramp must stay blue-hued")


class RedlineBlinkTests(unittest.TestCase):
    """The flash is the gradient, not a solid colour."""

    def _lit_frame(self, ctrl, strip, zones):
        ctrl._paint_rpm_zones(strip, zones, zones, 65535, duration_ms=0, batched=True)
        return strip.paints

    def test_the_flash_paints_every_zone_its_own_gradient_colour(self):
        strip = _FakeStrip("S", zones=8)
        ctrl = _controller([strip])
        paints = self._lit_frame(ctrl, strip, 8)
        hues = [p[1][0] for p in paints]
        self.assertEqual(len(set(hues)), 8, "every zone should differ — it's a ramp")
        self.assertEqual(hues[0], 21845)     # green at the idle end
        self.assertEqual(hues[-1], 0)        # red at the redline end

    def test_the_flash_matches_a_full_fill_apart_from_brightness(self):
        """Same picture, brighter — that's the whole idea."""
        a, b = _FakeStrip("A", zones=8), _FakeStrip("B", zones=8)
        ctrl = _controller([a, b])
        ctrl._paint_rpm_zones(a, 8, 8, RPM_FILL_BRIGHTNESS, duration_ms=40, batched=False)
        ctrl._paint_rpm_zones(b, 8, 8, 65535, duration_ms=0, batched=True)
        self.assertEqual([p[1][:2] for p in a.paints], [p[1][:2] for p in b.paints],
                         "hue/sat must be identical between fill and flash")
        self.assertTrue(all(f[1][2] < g[1][2] for f, g in zip(a.paints, b.paints)),
                        "the flash must be brighter than the fill it interrupts")

    def test_a_custom_ramp_flashes_that_ramp_not_red(self):
        strip = _FakeStrip("S", zones=6)
        ctrl = _controller([strip])
        ctrl.rpm_gradient = parse_rpm_gradient(("#0000ff", "#ffffff"))
        paints = self._lit_frame(ctrl, strip, 6)
        self.assertTrue(all(p[1][0] == 43690 for p in paints),
                        "a blue->white ramp must not flash red at the limiter")

    def test_the_flash_is_applied_as_one_update(self):
        """Zone-by-zone applies would tear the strip at 7.7 Hz."""
        strip = _FakeStrip("S", zones=8)
        ctrl = _controller([strip])
        paints = self._lit_frame(ctrl, strip, 8)
        self.assertEqual([p[3] for p in paints], [0] * 7 + [1])

    def test_the_fill_is_not_batched(self):
        """It has a 40 ms fade to smooth it; buffering would fight that."""
        strip = _FakeStrip("S", zones=8)
        ctrl = _controller([strip])
        ctrl.rpm_meter(100)
        self.assertTrue(all(p[3] == 1 for p in strip.paints))


class DirectionTests(unittest.TestCase):
    def test_the_flash_honours_the_fill_direction(self):
        for direction, expect_first in (("ltr", 0), ("rtl", 7)):
            strip = _FakeStrip("S", zones=8)
            ctrl = _controller([strip])
            ctrl.mz_startlights_direction = direction
            ctrl._paint_rpm_zones(strip, 8, 8, 65535, duration_ms=0, batched=True)
            with self.subTest(direction=direction):
                # The idle end (green) must land on the physical zone the
                # direction setting says it should.
                self.assertEqual(strip.paints[0][0], expect_first)
                self.assertEqual(strip.paints[0][1][0], 21845)


if __name__ == "__main__":
    unittest.main()
