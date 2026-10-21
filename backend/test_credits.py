"""
Unit tests for the per-job credit figure.

The point of these: a cost shown to the user must either be a real measurement or
be clearly marked as an estimate. Silently presenting a wrong number as fact is
worse than showing nothing.
"""

import unittest

from transcriber import estimate_credits, measured_cost


class TestEstimate(unittest.TestCase):

    def test_matches_the_measured_rate(self):
        # The calibration point: this exact lecture cost exactly 2,825 credits.
        self.assertAlmostEqual(estimate_credits(2542.64), 2825, delta=1)

    def test_scales_with_length(self):
        self.assertAlmostEqual(estimate_credits(60), estimate_credits(30) * 2, delta=0.2)

    def test_zero_length(self):
        self.assertEqual(estimate_credits(0), 0)


class TestMeasuredCost(unittest.TestCase):

    def test_plain_difference_is_used(self):
        cost, exact = measured_cost(1000, 1100, 90)
        self.assertEqual(cost, 100)
        self.assertTrue(exact, "a sensible difference should be reported as measured")

    def test_no_reading_falls_back_to_the_estimate(self):
        # This is what happens when the key lacks the "User: Read" permission.
        cost, exact = measured_cost(None, None, 60)
        self.assertAlmostEqual(cost, estimate_credits(60), delta=0.1)
        self.assertFalse(exact)

    def test_zero_difference_is_not_trusted(self):
        # A cached or stale reading can show no change although credits were spent.
        cost, exact = measured_cost(1000, 1000, 600)
        self.assertFalse(exact)
        self.assertGreater(cost, 0)

    def test_negative_difference_is_not_trusted(self):
        cost, exact = measured_cost(1000, 900, 600)
        self.assertFalse(exact)

    def test_wildly_large_difference_is_not_trusted(self):
        # Two jobs transcribing at once mix their spending into one reading.
        cost, exact = measured_cost(1000, 9000, 60)
        self.assertFalse(exact, "a difference far above the estimate must be rejected")
        self.assertAlmostEqual(cost, estimate_credits(60), delta=0.1)

    def test_slightly_above_estimate_is_still_trusted(self):
        # Real billing need not match the estimate exactly; only absurd values go.
        estimate = estimate_credits(60)
        cost, exact = measured_cost(1000, 1000 + int(estimate * 1.3), 60)
        self.assertTrue(exact)

    def test_no_audio_and_no_reading_gives_nothing(self):
        cost, exact = measured_cost(None, None, 0)
        self.assertIsNone(cost)
        self.assertFalse(exact)


if __name__ == "__main__":
    unittest.main(verbosity=2)
