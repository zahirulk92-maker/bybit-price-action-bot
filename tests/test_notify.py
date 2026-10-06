import unittest

from price_action_bot.notify import format_alert


class TelegramFormatTests(unittest.TestCase):
    def test_structured_alert_contains_status_facts_and_action(self):
        message = format_alert(
            "✅ BYBIT EXIT CONFIRMED",
            symbol="BTCUSDT",
            status="PROFIT",
            facts=[("💰 Net P&L", "+1.25 USDT")],
            action="Recorded in Trade Audit",
        )
        self.assertIn("Pair: BTCUSDT", message)
        self.assertIn("Status: PROFIT", message)
        self.assertIn("Net P&L: +1.25 USDT", message)
        self.assertTrue(message.endswith("Recorded in Trade Audit"))


if __name__ == "__main__":
    unittest.main()
