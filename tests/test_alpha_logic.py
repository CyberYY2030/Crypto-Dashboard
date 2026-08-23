import unittest

import alpha_logic


class AlphaLogicTests(unittest.TestCase):
    def test_passes_drawdown_when_alpha_open_is_down_over_90_pct(self):
        passed = alpha_logic.passes_drawdown_filter(
            current_price=0.08,
            alpha_open_price=1.00,
            ath_price=0.50,
            threshold_pct=90.0,
        )
        self.assertTrue(passed)

    def test_volume_expansion_ratio(self):
        ratio = alpha_logic.volume_expansion_ratio(
            current_volume=300000,
            baseline_volumes=[100000, 120000, 90000],
        )
        self.assertAlmostEqual(ratio, 3.0, places=2)

    def test_passes_volume_filter_requires_absolute_and_relative_thresholds(self):
        passed = alpha_logic.passes_volume_filter(
            current_volume=6_000_000,
            previous_volume=3_000_000,
            min_volume=5_000_000,
            min_ratio=1.5,
        )
        self.assertTrue(passed)

    def test_passes_volume_filter_rejects_equal_ratio_boundary(self):
        passed = alpha_logic.passes_volume_filter(
            current_volume=6_000_000,
            previous_volume=4_000_000,
            min_volume=5_000_000,
            min_ratio=1.5,
        )
        self.assertFalse(passed)

    def test_signal_label_prefers_breakout_when_volume_spikes(self):
        label = alpha_logic.classify_signal(
            volume_expansion_ratio=3.5,
            price_above_range=True,
            compression_score=0.2,
        )
        self.assertEqual(label, "first_volume_breakout")


if __name__ == "__main__":
    unittest.main()
