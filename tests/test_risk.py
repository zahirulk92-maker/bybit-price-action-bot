import unittest

from price_action_bot.risk import floor_to_step, position_size


class RiskTests(unittest.TestCase):
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

