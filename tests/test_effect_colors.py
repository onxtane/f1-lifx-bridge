"""Effect Customization — per-effect custom colours (Phase 2).

The load-bearing guarantee: an effect with no custom colour paints EXACTLY what
it always did (defaults untouched), so nobody who never opens the customizer sees
a change. Beyond that: a custom colour overrides hue+saturation while the effect
keeps its own brightness/kelvin, and the resolver reads the Sync-All colour or
the first per-light/per-zone stop as a single colour (Phase 2a).

The three controllers share the contract via different colour spaces: LIFX and
Nanoleaf use HSBK (`_fx`), Hue uses sRGB (`_fx_rgb`). We drive the resolvers with
lightweight stubs so no hardware/network is touched.
"""
import types
import unittest

from tests import harness  # noqa: F401 — sets sys.path for the app modules
from bridge_core import LocalLifxController, _override_hue_sat  # noqa: E402
from nanoleaf_controller import NanoleafController, _hex_to_hue_sat  # noqa: E402
from hue_controller import HueController, _hex_to_rgb  # noqa: E402

GREEN_HUE = 21845   # #00ff00 in the 0–65535 wheel


def lifx_fx(effect_colors, key, default, slot="main"):
    return LocalLifxController._fx(types.SimpleNamespace(effect_colors=effect_colors), key, default, slot)


def nl_fx(effect_colors, key, default, slot="main"):
    return NanoleafController._fx(types.SimpleNamespace(effect_colors=effect_colors), key, default, slot)


def hue_fx(effect_colors, key, default, slot="main"):
    return HueController._fx_rgb(types.SimpleNamespace(effect_colors=effect_colors), key, default, slot)


class TestDefaultsUntouched(unittest.TestCase):
    def test_no_config_returns_default_identity(self):
        d = [0, 65535, 65535, 3500]
        self.assertIs(lifx_fx({}, "red_flag", d), d)
        self.assertIs(nl_fx({}, "red_flag", d), d)
        self.assertEqual(hue_fx({}, "red_flag", (255, 0, 0)), (255, 0, 0))

    def test_other_effect_customized_leaves_this_one_default(self):
        ec = {"yellow_flag": {"mode": "all", "colors": {"main": "#123456"}}}
        d = [43690, 65535, 65535, 3500]
        self.assertEqual(lifx_fx(ec, "blue_flag", d), d)
        self.assertEqual(hue_fx(ec, "blue_flag", (0, 100, 255)), (0, 100, 255))

    def test_blank_or_bad_hex_falls_back(self):
        ec = {"red_flag": {"mode": "all", "colors": {"main": "not-a-colour"}}}
        d = [0, 65535, 65535, 3500]
        self.assertEqual(lifx_fx(ec, "red_flag", d), d)
        self.assertEqual(hue_fx({"red_flag": {"colors": {"main": ""}}}, "red_flag", (255, 0, 0)), (255, 0, 0))


class TestHsbkRecolour(unittest.TestCase):
    def test_overrides_hue_sat_keeps_brightness_and_kelvin(self):
        ec = {"red_flag": {"mode": "all", "colors": {"main": "#00ff00"}}}
        for fx in (lifx_fx, nl_fx):
            out = fx(ec, "red_flag", [0, 65535, 40000, 3500])
            self.assertEqual(out[0], GREEN_HUE)   # hue -> green
            self.assertEqual(out[1], 65535)        # sat from #00ff00
            self.assertEqual(out[2], 40000)        # brightness preserved
            self.assertEqual(out[3], 3500)         # kelvin preserved

    def test_white_default_recoloured_gains_saturation(self):
        ec = {"chequered_flag": {"mode": "all", "colors": {"a": "#ff0000"}}}
        out = lifx_fx(ec, "chequered_flag", [0, 0, 65535, 4500], "a")
        self.assertEqual(out[1], 65535)            # was white (sat 0), now saturated red


class TestSlotsAndModes(unittest.TestCase):
    def test_two_slot_effect_resolves_each(self):
        ec = {"chequered_flag": {"mode": "all", "colors": {"a": "#ff0000", "b": "#0000ff"}}}
        a = lifx_fx(ec, "chequered_flag", [0, 0, 65535, 4500], "a")
        b = lifx_fx(ec, "chequered_flag", [21845, 65535, 65535, 3500], "b")
        self.assertEqual(a[0], 0)          # red hue
        self.assertEqual(b[0], 43690)      # blue hue

    def test_per_light_mode_leaves_default_for_fx(self):
        # In per modes _fx keeps the effect default; per-target painting recolours
        # each light/zone in set_color_all (uncustomized targets = default).
        ec = {"start_lights": {"mode": "per_light", "per_light": {"Desk": "#00ff00"}}}
        d = [0, 65535, 50000, 3500]
        self.assertEqual(lifx_fx(ec, "start_lights", d), d)
        self.assertEqual(nl_fx(ec, "start_lights", d), d)

    def test_per_zone_mode_leaves_default_for_fx(self):
        ec = {"start_lights": {"mode": "per_zone", "per_zone": ["#0000ff"]}}
        d = [0, 65535, 65535, 3500]
        self.assertEqual(nl_fx(ec, "start_lights", d), d)

    def test_hue_rgb_slots(self):
        ec = {"chequered_flag": {"mode": "all", "colors": {"a": "#ffffff", "b": "#0000ff"}}}
        self.assertEqual(hue_fx(ec, "chequered_flag", (255, 255, 255), "a"), (255, 255, 255))
        self.assertEqual(hue_fx(ec, "chequered_flag", (0, 200, 0), "b"), (0, 0, 255))


class TestPerTargetOverride(unittest.TestCase):
    """The per-light/per-zone painter recolours each target via _override_hue_sat:
    hue+sat from the target's stop, brightness/kelvin from the animation frame."""

    def test_override_replaces_hue_sat_keeps_brightness(self):
        out = _override_hue_sat([0, 65535, 8000, 3500], "#0000ff")   # dim red frame -> blue
        self.assertEqual(out[0], 43690)   # blue hue
        self.assertEqual(out[2], 8000)    # dim brightness preserved (per-target pulse)
        self.assertEqual(out[3], 3500)

    def test_override_none_or_bad_is_identity(self):
        frame = [10922, 65535, 65535, 3500]
        self.assertEqual(_override_hue_sat(frame, None), frame)      # uncustomized target
        self.assertEqual(_override_hue_sat(frame, "nope"), frame)

    def test_zone_stops_map_by_index(self):
        # A per-zone strip: zone 0 -> red, zone 1 -> blue; frame brightness kept.
        stops = ["#ff0000", "#0000ff"]
        z0 = _override_hue_sat([0, 0, 40000, 4500], stops[0])
        z1 = _override_hue_sat([0, 0, 40000, 4500], stops[1])
        self.assertEqual((z0[0], z0[2]), (0, 40000))
        self.assertEqual((z1[0], z1[2]), (43690, 40000))


class TestHexHelpers(unittest.TestCase):
    def test_hue_hex_to_rgb(self):
        self.assertEqual(_hex_to_rgb("#00ccff"), (0, 204, 255))
        self.assertEqual(_hex_to_rgb("#0f0"), (0, 255, 0))
        self.assertIsNone(_hex_to_rgb("nope"))

    def test_nl_hex_to_hue_sat(self):
        self.assertEqual(_hex_to_hue_sat("#ff0000"), (0, 65535))
        self.assertIsNone(_hex_to_hue_sat("zzz"))


if __name__ == "__main__":
    unittest.main()
