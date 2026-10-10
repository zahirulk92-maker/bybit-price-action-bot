import unittest

from price_action_bot.risk import (
    entry_within_trigger_boundary,
    floor_to_step,
    net_reward_risk,
    position_size,
)


class RiskTests(unittest.TestCase):
    def test_entry_boundary_requires_current_price_to_hold_the_trigger(self):
        self.assertTrue(entry_within_trigger_boundary(100.01, 100, "Buy", 0.0002))
        self.assertFalse(entry_within_trigger_boundary(99.99, 100, "Buy", 0.0002))
        self.assertFalse(entry_within_trigger_boundary(100.03, 100, "Buy", 0.0002))
        self.assertTrue(entry_within_trigger_boundary(99.99, 100, "Sell", 0.0002))

    def test_net_reward_risk_rejects_wrong_stop_and_includes_costs(self):
        self.assertEqual(net_reward_risk(100, 101, 104, "Buy", 0.00055, 0.0002), 0.0)
        self.assertLess(net_reward_risk(100, 99, 102, "Buy", 0.00055, 0.0002), 2.0)

    def test_position_size_rejects_wrong_side_stop_when_side_is_known(self):
        with self.assertRaisesRegex(ValueError, "Buy stop must be below entry"):
            position_size(1_000, 0.01, 100, 101, 0.001, 0.001, 1_000, side="Buy")

    def test_floor_to_step(self):
        self.assertEqual(floor_to_step(0.01669, 0.001), 0.016)

    def test_position_size_uses_stop_distance(self):
        qty = position_size(
            equity=1000,
            risk_fraction=0.005,
            entry=85_300,
            stop=85_000,
            qty_step=0.001,
            min_qty=0.001,
            max_qty=100,
        )
        self.assertEqual(qty, 0.016)

    def test_below_minimum_returns_zero(self):
        qty = position_size(100, 0.001, 85_300, 80_000, 0.001, 0.001, 100)
        self.assertEqual(qty, 0.0)


if __name__ == "__main__":
    unittest.main()

