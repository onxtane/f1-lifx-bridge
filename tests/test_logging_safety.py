"""Logging must never take down a listener thread (#76).

A cp1252 console raises UnicodeEncodeError on any character it can't map. An
arrow in a DiRT Rally / Forza setup hint was enough to kill the bridge, so
log() has to swallow print failures while still delivering the line to the UI.
"""
import io
import sys
import unittest

from tests import harness  # noqa: F401  — sets sys.path for the app modules
from bridge_core import F1LifxBridgeCore  # noqa: E402


def _cp1252_stdout():
    """A console stream that cannot encode characters outside cp1252."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


class _BrokenStdout:
    """Stdout that fails on write (closed pipe, detached console)."""

    def write(self, *_a):
        raise OSError("pipe closed")

    def flush(self):
        pass


class LoggingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.bridge = F1LifxBridgeCore(dry_run=True, log_callback=self.received.append)

    def test_unencodable_chars_do_not_raise(self):
        # Both of these previously crashed the bridge on a cp1252 console.
        original, sys.stdout = sys.stdout, _cp1252_stdout()
        try:
            self.bridge.log("In-game: Options → Accessibility → UDP Telemetry")
            self.bridge.log("[DR2] Crash - Δspeed=5.0 m/s")
        finally:
            sys.stdout = original

    def test_callback_still_fires_when_print_fails(self):
        original, sys.stdout = sys.stdout, _cp1252_stdout()
        try:
            self.bridge.log("arrow → here")
        finally:
            sys.stdout = original
        self.assertEqual(self.received, ["arrow → here"])

    def test_broken_stdout_does_not_raise(self):
        original, sys.stdout = sys.stdout, _BrokenStdout()
        try:
            self.bridge.log("anything")
        finally:
            sys.stdout = original
        self.assertEqual(self.received, ["anything"])


if __name__ == "__main__":
    unittest.main()
