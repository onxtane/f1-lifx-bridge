"""A brand that doesn't implement an effect must be skipped, not reported (#49).

Found by running Assetto Corsa against real hardware: every crash logged
`[NANOLEAF ERROR] crash: 'NanoleafController' object has no attribute 'crash'`.
Neither Nanoleaf nor Hue implements crash — only LIFX does — so DiRT Rally and
Forza Horizon 6 have been raising an error banner (#73) at those owners on
every impact since they shipped, for something they can't do anything about.

Not implementing an effect is a difference in capability, not a fault.
"""
import unittest

from tests import harness  # noqa: F401  — sets sys.path for the app modules
from bridge_core import F1LifxBridgeCore  # noqa: E402


class _Brand:
    """A controller that only knows some effects."""

    def __init__(self, *effects):
        self.calls = []
        for name in effects:
            setattr(self, name, lambda *a, _n=name: self.calls.append((_n, a)))


class _Bridge(F1LifxBridgeCore):
    def __init__(self):
        super().__init__(dry_run=True)
        self.logs = []

    def log(self, message):
        self.logs.append(message)


class MissingEffectTests(unittest.TestCase):
    def setUp(self):
        self.bridge = _Bridge()
        self.bridge.lifx = _Brand("crash", "lights_out")
        self.bridge.nanoleaf = _Brand("lights_out")      # no crash, like the real one
        self.bridge.hue = _Brand("lights_out")           # nor this one

    def test_a_brand_without_the_effect_is_skipped_silently(self):
        self.bridge._fire("crash")
        self.assertEqual(self.bridge.nanoleaf.calls, [])
        errors = [l for l in self.bridge.logs if "ERROR" in l]
        self.assertEqual(errors, [], "reported a capability gap as an error")

    def test_the_brands_that_do_have_it_still_fire(self):
        self.bridge._fire("crash")
        self.assertEqual(self.bridge.lifx.calls, [("crash", ())])

    def test_an_effect_every_brand_has_reaches_all_of_them(self):
        self.bridge._fire("lights_out")
        for brand in (self.bridge.lifx, self.bridge.nanoleaf, self.bridge.hue):
            self.assertEqual(brand.calls, [("lights_out", ())])

    def test_a_real_failure_is_still_reported(self):
        """Skipping absent effects must not swallow genuine errors."""
        def boom(*_a):
            raise RuntimeError("bulb unplugged")
        self.bridge.nanoleaf.crash = boom
        self.bridge._fire("crash")
        errors = [l for l in self.bridge.logs if "ERROR" in l]
        self.assertEqual(len(errors), 1)
        self.assertIn("bulb unplugged", errors[0])
        self.assertIn("NANOLEAF", errors[0])


if __name__ == "__main__":
    unittest.main()
