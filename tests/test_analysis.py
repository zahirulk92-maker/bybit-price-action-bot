import unittest

from price_action_bot.analysis import detect_pattern, detect_trade_reversal, reward_risk, setup_checklist
from price_action_bot.models import Candle, MarketContext, Zone


def candle(index, open_, high, low, close, volume=100):
    return Candle(index * 300_000, open_, high, low, close, volume)


class PatternTests(unittest.TestCase):
    def setUp(self):
        self.context = MarketContext(
            bias="bullish",
            support=Zone(100, 99, 101, "support"),
            resistance=Zone(110, 109, 111, "resistance"),
            atr=4,
        )
        self.history = [candle(i, 103, 104, 102, 103.5, 100) for i in range(20)]

    def test_bullish_pin_bar_with_volume_is_detected(self):
        candles = self.history + [
            candle(20, 103, 104, 102, 102.5, 100),
            candle(21, 102.5, 103, 101.5, 102, 100),
            candle(22, 100.2, 101.0, 98.0, 100.8, 150),
        ]
        signal = detect_pattern(candles, self.context, 1.2)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.name, "bullish_pin_bar")
        self.assertEqual(signal.side, "Buy")
        self.assertLess(signal.stop, 98.0)

    def test_weak_volume_rejects_pattern(self):
        candles = self.history + [
            candle(20, 103, 104, 102, 102.5, 100),
            candle(21, 102.5, 103, 101.5, 102, 100),
            candle(22, 100.2, 101.0, 98.0, 100.8, 110),
        ]
        self.assertIsNone(detect_pattern(candles, self.context, 1.2))
        decision = setup_checklist(candles, self.context, 1.2, 1.5)
        checks = {item["key"]: item for item in decision["checks"]}
        self.assertEqual(checks["zone"]["status"], "pass")
        self.assertEqual(checks["pattern"]["status"], "pass")
        self.assertEqual(checks["volume"]["status"], "wait")
        self.assertIn("volume", decision["summary"].lower())

    def test_checklist_explains_when_price_is_outside_zone(self):
        candles = self.history + [
            candle(20, 105, 106, 104, 105.5),
            candle(21, 105.5, 106, 104.5, 105),
            candle(22, 105, 106, 104, 105.5),
        ]
        decision = setup_checklist(candles, self.context, 1.2, 1.5)
        checks = {item["key"]: item for item in decision["checks"]}
        self.assertEqual(checks["zone"]["status"], "wait")
        self.assertIn("target zone", decision["summary"].lower())

    def test_reward_risk(self):
        self.assertEqual(reward_risk(100, 98, 104, "Buy"), 2.0)
        self.assertEqual(reward_risk(100, 101, 104, "Buy"), 0.0)
        self.assertEqual(reward_risk(100, 99, 96, "Buy"), 0.0)
        self.assertEqual(reward_risk(100, 99, 96, "Sell"), 0.0)

    def test_trade_reversal_is_symmetric_for_both_sides(self):
        candles = self.history + [
            candle(20, 100, 101, 98.5, 99, 100),
            candle(21, 99, 99.5, 97.5, 98, 100),
            candle(22, 97.8, 101.2, 97.5, 100.8, 240),
        ]
        short_exit = detect_trade_reversal(candles, "Sell", 1.2)
        self.assertIsNotNone(short_exit)
        self.assertEqual(short_exit["pattern"], "bullish_engulfing")

        long_candles = self.history + [
            candle(20, 98, 101, 97.5, 100, 100),
            candle(21, 100, 101.5, 99.5, 101, 100),
            candle(22, 101.2, 101.5, 97, 97.4, 240),
        ]
        long_exit = detect_trade_reversal(long_candles, "Buy", 1.2)
        self.assertIsNotNone(long_exit)
        self.assertEqual(long_exit["pattern"], "bearish_engulfing")
        self.assertEqual(reward_risk(100, 102, 96, "Sell"), 2.0)


if __name__ == "__main__":
    unittest.main()
