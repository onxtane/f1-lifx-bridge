"""Recording bridge subclasses — capture dispatch decisions without hardware.

Each subclass replaces the effect sinks (_fire and the *_bridge flag loops) with
recorders, so feeding a packet produces a deterministic list of (effect, args)
tuples instead of driving lights or spawning animation threads.
"""
import os
import sys

# Allow `python -m unittest discover -s tests` from the repo root to import the
# app modules that live one level up.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge_core import F1LifxBridgeCore   # noqa: E402
from dr2_bridge import DR2BridgeCore        # noqa: E402


class RecordingF1Bridge(F1LifxBridgeCore):
    """F1 bridge that records dispatches to .dispatches as (effect, args) tuples."""

    def __init__(self):
        super().__init__(dry_run=True)
        self.dispatches = []
        self.enabled_events = None   # None = every event enabled

    def reset(self):
        self.dispatches.clear()
        return self

    # Effect sinks → recorders
    def _fire(self, method, *args):
        self.dispatches.append((method, args))

    # FIA flag loops fire continuously on a thread in production; here we just
    # record the decision so tests stay deterministic and thread-free.
    def yellow_flag_bridge(self):
        self.dispatches.append(("yellow_flag", ()))

    def blue_flag_bridge(self):
        self.dispatches.append(("blue_flag", ()))

    def red_flag_bridge(self):
        self.dispatches.append(("red_flag", ()))

    def neutral_bridge(self):
        self.dispatches.append(("neutral", ()))

    # Silence per-packet logging during tests.
    def log(self, message):
        pass


class RecordingDR2Bridge(DR2BridgeCore):
    """DiRT Rally 2.0 bridge that records dispatches the same way."""

    def __init__(self):
        super().__init__(dry_run=True)
        self.dispatches = []
        self.enabled_events = None

    def reset(self):
        self.dispatches.clear()
        return self

    def _fire(self, method, *args):
        self.dispatches.append((method, args))

    def neutral_bridge(self):
        self.dispatches.append(("neutral", ()))

    def log(self, message):
        pass
