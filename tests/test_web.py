import unittest
from unittest.mock import patch

from fastapi import HTTPException

from price_action_bot import web
from price_action_bot.models import Candle


class ChartApiTests(unittest.TestCase):
    def setUp(self):
        web.chart_cache.clear()
        self.market = {
            "symbol": "BTCUSDT",
            "price": 100.0,
            "bias": "bullish",
            "support": 95.0,
            "resistance": 110.0,
            "signal_state": "SCAN",
            "updated_at_ms": 1,
        }

    def test_chart_returns_closed_candles_and_levels(self):
        candles = [Candle(1, 100, 104, 98, 102, 50)]
        with patch.object(web.store, "market_snapshots", return_value=[self.market]), patch.object(
            web.chart_gateway, "candles", return_value=candles
        ):
            result = web.api_chart("btcusdt", "5", 160, "test")
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["support"], 95.0)
        self.assertEqual(result["candles"][0]["close"], 102)

    def test_chart_rejects_unknown_interval(self):
        with self.assertRaises(HTTPException) as raised:
            web.api_chart("BTCUSDT", "1", 160, "test")
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
