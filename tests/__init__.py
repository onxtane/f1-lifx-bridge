"""Replay-based integration tests for GridGlow packet parsing + effect dispatch (#36).

These tests feed crafted UDP packet bytes through the real parse/dispatch pipeline
in bridge_core.py / dr2_bridge.py and assert the correct effect is dispatched — with
no LIFX/Nanoleaf/Hue hardware and no live network. Pure stdlib unittest.

Run from the repo root:
    python -m unittest discover -s tests -v
"""
