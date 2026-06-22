"""Unit tests for marshal-zone → per-sector flag mapping (live sector status, #12)."""
import unittest

from tests import fixtures as fx
from bridge_core import (
    parse_session_sector_flags,
    FIA_FLAG_NONE, FIA_FLAG_GREEN, FIA_FLAG_BLUE, FIA_FLAG_YELLOW,
)

HEADER_SIZE = 29  # F1 25 header


def sectors(zones):
    return parse_session_sector_flags(fx.f1_session_zones(zones), HEADER_SIZE)


class SectorFlagTests(unittest.TestCase):
    def test_yellow_in_sector_2_only(self):
        # zoneStart 0.5 → middle third
        self.assertEqual(sectors([(0.5, FIA_FLAG_YELLOW)]),
                         [FIA_FLAG_NONE, FIA_FLAG_YELLOW, FIA_FLAG_NONE])

    def test_one_zone_per_sector(self):
        self.assertEqual(
            sectors([(0.1, FIA_FLAG_GREEN), (0.5, FIA_FLAG_YELLOW), (0.9, FIA_FLAG_BLUE)]),
            [FIA_FLAG_GREEN, FIA_FLAG_YELLOW, FIA_FLAG_BLUE])

    def test_highest_priority_wins_within_a_sector(self):
        # Two zones both in sector 1 (thirds boundary at 0.333); green then yellow → yellow.
        self.assertEqual(
            sectors([(0.05, FIA_FLAG_GREEN), (0.20, FIA_FLAG_YELLOW)]),
            [FIA_FLAG_YELLOW, FIA_FLAG_NONE, FIA_FLAG_NONE])

    def test_third_boundaries(self):
        # 0.0 → S1, 0.34 → S2, 0.67 → S3
        self.assertEqual(
            sectors([(0.0, FIA_FLAG_YELLOW), (0.34, FIA_FLAG_GREEN), (0.67, FIA_FLAG_BLUE)]),
            [FIA_FLAG_YELLOW, FIA_FLAG_GREEN, FIA_FLAG_BLUE])

    def test_invalid_flag_ignored(self):
        self.assertEqual(sectors([(0.5, -1)]),
                         [FIA_FLAG_NONE, FIA_FLAG_NONE, FIA_FLAG_NONE])

    def test_all_clear(self):
        self.assertEqual(
            sectors([(0.1, FIA_FLAG_NONE), (0.5, FIA_FLAG_NONE), (0.9, FIA_FLAG_NONE)]),
            [FIA_FLAG_NONE, FIA_FLAG_NONE, FIA_FLAG_NONE])

    def test_no_zones_returns_none(self):
        self.assertIsNone(sectors([]))

    def test_out_of_range_zone_start_clamped(self):
        # zoneStart >= 1.0 clamps into S3 rather than indexing out of range
        self.assertEqual(sectors([(1.0, FIA_FLAG_YELLOW)]),
                         [FIA_FLAG_NONE, FIA_FLAG_NONE, FIA_FLAG_YELLOW])


if __name__ == "__main__":
    unittest.main(verbosity=2)
