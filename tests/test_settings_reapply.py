"""Persisted controller settings must reach the LIFX / Nanoleaf controllers
whether they already exist when the bridge is built or are created later during
discovery. _apply_pending_to_controllers() is the single choke point both paths
call, so every controller setting is applied consistently — that's the hardening
against the "restart shows it but it tests as default until you change it" bug.
"""
import unittest

from tests import harness  # noqa: F401 — sets sys.path for the app modules
import bridge_runner        # noqa: E402
import bridge_core          # noqa: E402


def _runner():
    # No callbacks (they default to no-ops). Patch the settings read so __init__
    # doesn't pick anything up from the real files — we set _pending_* directly.
    orig = bridge_runner.BridgeRunner._read_json
    bridge_runner.BridgeRunner._read_json = (
        lambda self, path, default=None: {} if default is None else default)
    try:
        return bridge_runner.BridgeRunner()
    finally:
        bridge_runner.BridgeRunner._read_json = orig


class _Ctrl:
    """A controller stand-in that records whatever gets assigned to it."""


class _Bridge:
    def __init__(self, lifx=None, nl=None):
        self.lifx = lifx
        self.nanoleaf = nl
        self.hue = None


class ReapplyTests(unittest.TestCase):
    def test_every_pending_setting_lands_on_lifx(self):
        r = _runner()
        r._module = bridge_core
        r._pending_brightness = (1000, 60000)
        r._pending_stagger = (True, 40)
        r._pending_idle = ([100, 200, 30000, 3500], True)
        r._pending_mz_startlights = ("rtl", "solid")
        r._pending_rpm_gradient = ["#00ff00", "#ff0000"]
        r._curves = {"Start Lights": {"points": [[0, 0], [1, 1]], "duration_ms": 250}}
        r._effect_colors = {"red_flag": {"mode": "all", "colors": {"main": "#0000ff"}}}
        lifx = _Ctrl()
        r.bridge = _Bridge(lifx=lifx)

        r._apply_pending_to_controllers()

        self.assertEqual((lifx.brightness_min, lifx.brightness_max), (1000, 60000))
        self.assertEqual(lifx.stagger_ms, 40)
        self.assertEqual(lifx.idle_hsbk, [100, 200, 30000, 3500])
        self.assertTrue(lifx.idle_pulse)
        self.assertEqual((lifx.mz_startlights_direction, lifx.mz_startlights_mode), ("rtl", "solid"))
        self.assertEqual(lifx.rpm_gradient, bridge_core.parse_rpm_gradient(["#00ff00", "#ff0000"]))
        self.assertIn("Start Lights", lifx.curves)
        self.assertIn("red_flag", lifx.effect_colors)

    def test_stagger_disabled_zeroes_ms(self):
        r = _runner()
        r._pending_stagger = (False, 40)
        lifx = _Ctrl()
        r.bridge = _Bridge(lifx=lifx)
        r._apply_pending_to_controllers()
        self.assertEqual(lifx.stagger_ms, 0)

    def test_missing_controller_is_a_safe_noop(self):
        r = _runner()
        r._pending_rpm_gradient = ["#00ff00"]
        r.bridge = _Bridge(lifx=None, nl=None)
        r._apply_pending_to_controllers()   # must not raise

    def test_nanoleaf_gets_the_shared_settings(self):
        r = _runner()
        r._effect_colors = {"red_flag": {"mode": "all", "colors": {"main": "#0000ff"}}}
        r._curves = {"X": {"points": [[0, 0]], "duration_ms": 100}}
        r._pending_mz_startlights = ("ltr", "sweep")
        nl = _Ctrl()
        r.bridge = _Bridge(nl=nl)
        r._apply_pending_to_controllers()
        self.assertIn("red_flag", nl.effect_colors)
        self.assertIn("X", nl.curves)
        self.assertEqual(nl.mz_startlights_direction, "ltr")


if __name__ == "__main__":
    unittest.main()
