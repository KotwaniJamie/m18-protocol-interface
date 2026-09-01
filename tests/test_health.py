"""
Regression tests for health() / health_data().

No hardware needed: read_id() is stubbed with canned register values (see
fixtures.py), so these exercise the arithmetic and formatting only.

    python3 tests/test_health.py
    python3 -m unittest discover tests

health()'s printed output is pinned against tests/golden_health.txt. If a
change is meant to alter that output, regenerate it deliberately:

    python3 tests/test_health.py --update-golden
"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "golden_health.txt")


def render_all():
    out = []
    for name, values in fixtures.CASES.items():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fixtures.build(values).health()
        out.append(f"########## {name} ##########\n{buf.getvalue()}")
    return "\n".join(out)


class TestPrintedOutput(unittest.TestCase):
    def test_matches_golden(self):
        """health() prints exactly what it has always printed."""
        with open(GOLDEN) as fh:
            expected = fh.read()
        self.assertEqual(render_all(), expected)


class TestUnusedPack(unittest.TestCase):
    """Issue #47: a pack that never drew >10A has tool_time == 0."""

    def test_no_division_by_zero(self):
        data = fixtures.build(fixtures.CASES["unused"]).health_data()
        self.assertEqual(data["tool_time_s"]["value"], 0)
        self.assertTrue(all(b["percent"] == 0 for b in data["discharge_buckets"]))

    def test_health_still_prints(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fixtures.build(fixtures.CASES["unused"]).health()
        self.assertNotIn("Failed with error", buf.getvalue())


class TestValidity(unittest.TestCase):
    """Sentinel readings must be flagged, and must not be laundered."""

    def setUp(self):
        self.dead = fixtures.build(fixtures.CASES["dead_sentinels"]).health_data()
        self.good = fixtures.build(fixtures.CASES["normal"]).health_data()

    def test_sentinels_flagged_invalid(self):
        for field in ("total_discharge_as", "charge_count_total",
                      "total_charge_time", "days_since_first_charge"):
            self.assertFalse(self.dead[field]["valid"], field)

    def test_derived_values_inherit_invalidity(self):
        """A value derived from a sentinel is never more trusted than it."""
        self.assertFalse(self.dead["total_discharge_ah"]["valid"])
        self.assertFalse(self.dead["total_discharge_cycles"]["valid"])
        self.assertFalse(self.dead["tool_time_s"]["valid"])

    def test_epoch_dates_flagged(self):
        """A date register never written reads as epoch zero, not 1970."""
        self.assertFalse(self.dead["days_since_last_tool_use"]["valid"])

    def test_healthy_pack_is_valid(self):
        for field in ("pack_voltage_v", "cell_imbalance_mv", "tool_time_s",
                      "total_discharge_ah", "total_discharge_cycles"):
            self.assertTrue(self.good[field]["valid"], field)


class TestUnknownType(unittest.TestCase):
    def test_cycles_not_derivable(self):
        data = fixtures.build(fixtures.CASES["unknown_type"]).health_data()
        self.assertFalse(data["capacity_ah"]["valid"])
        self.assertIsNone(data["total_discharge_cycles"]["value"])
        self.assertFalse(data["total_discharge_cycles"]["valid"])


class TestRegisterLookup(unittest.TestCase):
    def test_no_positional_coupling(self):
        """
        Fields are keyed by register id, so an extra register in the read
        must not shift anything. Ask for the buckets in reverse and the
        answer should be unchanged.
        """
        import m18
        values = fixtures.CASES["normal"]
        bat = fixtures.build(values)
        baseline = bat.health_data()

        original = m18.HEALTH_BUCKET_REGISTERS
        try:
            # read_id is stubbed by id, so ordering is the only variable
            m18.HEALTH_BUCKET_REGISTERS = tuple(reversed(original))
            shuffled = fixtures.build(values).health_data()
        finally:
            m18.HEALTH_BUCKET_REGISTERS = original

        self.assertEqual(baseline["tool_time_s"]["value"],
                         shuffled["tool_time_s"]["value"])
        self.assertEqual(baseline["pack_voltage_v"]["value"],
                         shuffled["pack_voltage_v"]["value"])


if __name__ == "__main__":
    if "--update-golden" in sys.argv:
        with open(GOLDEN, "w") as fh:
            fh.write(render_all())
        print(f"wrote {GOLDEN}")
    else:
        unittest.main()
