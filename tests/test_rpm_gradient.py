"""The RPM meter's colour ramp is user-configurable (#71).

The load-bearing test here is that the default ramp still paints exactly what
the old hard-coded `hue = 21845 * (1 - t)` line did — this shipped in v0.10.0
and nobody who never touches the setting should see their strip change.

The rest guards the things that make a gradient look right rather than merely
work: hue takes the short way round the wheel (green -> red through yellow, not
through blue), and a white stop pales out instead of cycling on its way there.
"""
import unittest

from tests import harness  # noqa: F401  — sets sys.path for the app modules
from bridge_core import (  # noqa: E402
    RPM_FILL_BRIGHTNESS, RPM_GRADIENT_DEFAULT,
    hex_to_hsv, hsv_to_hex, parse_rpm_gradient, rpm_gradient_swatch,
    sample_rpm_gradient,
)

GREEN, RED, BLUE, WHITE = 21845, 0, 43690, None


def _fill(stops, zone_count):
    """What rpm_meter paints per zone: (hue, sat, brightness-before-scaling)."""
    grad = parse_rpm_gradient(stops)
    out = []
    for pos in range(zone_count):
        t = pos / max(zone_count - 1, 1)
        hue, sat, val = sample_rpm_gradient(grad, t)
        out.append((hue, sat, round(val * RPM_FILL_BRIGHTNESS)))
    return out


class DefaultIsUnchangedTests(unittest.TestCase):
    """v0.10.0 shipped a hard-coded ramp. Anyone who leaves it alone keeps it."""

    def test_default_matches_the_old_hardcoded_formula_exactly(self):
        for zone_count in (1, 2, 8, 16, 30, 82):
            with self.subTest(zones=zone_count):
                old = [(round(21845 * (1.0 - pos / max(zone_count - 1, 1))),
                        65535, 60000)
                       for pos in range(zone_count)]
                self.assertEqual(_fill(RPM_GRADIENT_DEFAULT, zone_count), old)

    def test_default_redline_blink_colour_is_unchanged(self):
        """The blink uses the top stop; on the default that has to still be red."""
        hue, sat, val = parse_rpm_gradient(RPM_GRADIENT_DEFAULT)[-1]
        self.assertEqual((hue, sat, round(val * 65535)), (0, 65535, 65535))

    def test_the_fill_stays_dimmer_than_the_redline_blink(self):
        """The blink reads as brighter than the fill it interrupts — that's the
        point of the meter running at 60000 rather than full."""
        self.assertLess(RPM_FILL_BRIGHTNESS, 65535)


class HueTravelTests(unittest.TestCase):
    def test_green_to_red_passes_through_yellow(self):
        """The short way round. Through blue would be the same endpoints and a
        completely different-looking strip."""
        mid_hue, _s, _v = sample_rpm_gradient(parse_rpm_gradient(("#00ff00", "#ff0000")), 0.5)
        self.assertEqual(mid_hue, 10922)                 # ~60 deg = yellow

    def test_red_to_blue_passes_through_magenta_not_green(self):
        mid_hue, _s, _v = sample_rpm_gradient(parse_rpm_gradient(("#ff0000", "#0000ff")), 0.5)
        self.assertEqual(mid_hue, 54613)                 # ~300 deg = magenta

    def test_hue_never_leaves_the_wheel(self):
        for stops in (("#ff0000", "#0000ff"), ("#0000ff", "#ff0000"),
                      ("#ff00ff", "#00ff00")):
            grad = parse_rpm_gradient(stops)
            for i in range(21):
                hue, _s, _v = sample_rpm_gradient(grad, i / 20)
                with self.subTest(stops=stops, t=i / 20):
                    self.assertGreaterEqual(hue, 0)
                    self.assertLess(hue, 65536)


class DesaturatedStopTests(unittest.TestCase):
    def test_blue_to_white_pales_out_without_changing_hue(self):
        """White has no hue. Interpolating toward its nominal 0 would sweep the
        strip through cyan/green while desaturating — it must just go pale."""
        grad = parse_rpm_gradient(("#0000ff", "#ffffff"))
        hues, sats = [], []
        for i in range(11):
            hue, sat, _v = sample_rpm_gradient(grad, i / 10)
            hues.append(hue)
            sats.append(sat)
        self.assertEqual(set(hues), {BLUE})              # hue never moves
        self.assertEqual(sats[0], 65535)
        self.assertEqual(sats[-1], 0)
        self.assertEqual(sats, sorted(sats, reverse=True))   # and only ever fades

    def test_white_to_red_takes_the_red_hue_throughout(self):
        grad = parse_rpm_gradient(("#ffffff", "#ff0000"))
        hues = [sample_rpm_gradient(grad, i / 10)[0] for i in range(11)]
        self.assertEqual(set(hues), {RED})


class BrightnessTests(unittest.TestCase):
    def test_a_dark_stop_renders_dark(self):
        """The picker must not lie: pick a dark red, get a dark red."""
        grad = parse_rpm_gradient(("#330000", "#ff0000"))
        first = sample_rpm_gradient(grad, 0.0)
        last = sample_rpm_gradient(grad, 1.0)
        self.assertAlmostEqual(first[2], 0.2, places=2)
        self.assertEqual(last[2], 1.0)

    def test_intensity_ramp_climbs_monotonically(self):
        grad = parse_rpm_gradient(("#330000", "#ff0000"))
        vals = [sample_rpm_gradient(grad, i / 10)[2] for i in range(11)]
        self.assertEqual(vals, sorted(vals))
        self.assertEqual(len(set(vals)), len(vals))      # actually ramps, not flat


class MultiStopTests(unittest.TestCase):
    def test_three_stops_put_the_middle_colour_in_the_middle(self):
        grad = parse_rpm_gradient(("#00ff00", "#ffff00", "#ff0000"))
        self.assertEqual(sample_rpm_gradient(grad, 0.5)[0], 10922)   # yellow

    def test_stops_are_evenly_spaced_and_endpoints_are_exact(self):
        grad = parse_rpm_gradient(("#00ff00", "#0000ff", "#ff0000"))
        self.assertEqual(sample_rpm_gradient(grad, 0.0)[0], GREEN)
        self.assertEqual(sample_rpm_gradient(grad, 0.5)[0], BLUE)
        self.assertEqual(sample_rpm_gradient(grad, 1.0)[0], RED)

    def test_t_of_one_lands_on_the_last_stop_not_past_it(self):
        """The int(pos) segment index would run off the end at t == 1."""
        for count in range(2, 7):
            stops = ["#00ff00"] * (count - 1) + ["#ff0000"]
            with self.subTest(stops=count):
                self.assertEqual(sample_rpm_gradient(parse_rpm_gradient(stops), 1.0)[0], RED)


class ParsingTests(unittest.TestCase):
    def test_junk_falls_back_to_the_default_rather_than_raising(self):
        """A malformed setting must not be able to kill a listener thread (#76)."""
        default = parse_rpm_gradient(RPM_GRADIENT_DEFAULT)
        for junk in (None, [], ["#00ff00"], "nonsense", ["zzzzzz", "#GG0000"],
                     [1, 2], [None, None], {}, ["#00ff00", None]):
            with self.subTest(junk=junk):
                self.assertEqual(parse_rpm_gradient(junk), default)

    def test_shorthand_and_bare_hex_are_accepted(self):
        self.assertEqual(hex_to_hsv("#00f"), hex_to_hsv("#0000ff"))
        self.assertEqual(hex_to_hsv("00ff00"), hex_to_hsv("#00ff00"))

    def test_a_single_bad_stop_doesnt_poison_the_good_ones(self):
        self.assertEqual(parse_rpm_gradient(["#00ff00", "zzz", "#ff0000"]),
                         parse_rpm_gradient(["#00ff00", "#ff0000"]))

    def test_hex_round_trips_through_hsv(self):
        for hex_color in ("#00ff00", "#ff0000", "#0000ff", "#ffffff",
                          "#330000", "#8b7cf6", "#000000"):
            with self.subTest(hex=hex_color):
                self.assertEqual(hsv_to_hex(*hex_to_hsv(hex_color)), hex_color)


class SwatchTests(unittest.TestCase):
    """The settings preview is painted from these, so they are what the user
    is promised the strip will do."""

    def test_swatch_is_hex_and_spans_the_ramp(self):
        swatch = rpm_gradient_swatch(RPM_GRADIENT_DEFAULT, 24)
        self.assertEqual(len(swatch), 24)
        for entry in swatch:
            self.assertRegex(entry, r"^#[0-9a-f]{6}$")
        self.assertEqual(swatch[0], "#00ff00")
        self.assertEqual(swatch[-1], "#ff0000")

    def test_the_preview_shows_the_bright_yellow_the_strip_shows(self):
        """A CSS gradient would put a muddy #808000 here. The whole reason the
        swatch is computed in the backend is that this must be #ffff00."""
        swatch = rpm_gradient_swatch(("#00ff00", "#ff0000"), 3)
        self.assertEqual(swatch[1], "#ffff00")

    def test_swatch_survives_junk_and_silly_sample_counts(self):
        for samples in (0, 1, 2, 100):
            with self.subTest(samples=samples):
                self.assertGreaterEqual(len(rpm_gradient_swatch(None, samples)), 2)


if __name__ == "__main__":
    unittest.main()
