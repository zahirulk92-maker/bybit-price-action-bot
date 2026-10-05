import unittest

from price_action_bot.universe import select_symbols


class UniverseTests(unittest.TestCase):
    def test_btc_eth_are_kept_and_new_listing_is_excluded(self):
        now = 2_000_000_000_000
        old = now - 40 * 24 * 60 * 60 * 1000
        new = now - 2 * 24 * 60 * 60 * 1000
        instruments = [
            {
                "symbol": symbol,
                "baseCoin": symbol.removesuffix("USDT"),
                "quoteCoin": "USDT",
                "status": "Trading",
                "contractType": "LinearPerpetual",
                "launchTime": str(new if symbol == "NEWUSDT" else old),
            }
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "NEWUSDT")
        ]
        tickers = [
            {
                "symbol": symbol,
                "bid1Price": "100",
                "ask1Price": "100.01",
                "turnover24h": str(turnover),
                "openInterestValue": "1000000",
            }
            for symbol, turnover in (
                ("SOLUSDT", 9_000_000),
                ("NEWUSDT", 8_000_000),
                ("BTCUSDT", 7_000_000),
                ("ETHUSDT", 6_000_000),
            )
        ]
        selected = select_symbols(tickers, instruments, size=3, now_ms=now)
        self.assertEqual(selected[:2], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(selected[2], "SOLUSDT")
        self.assertNotIn("NEWUSDT", selected)


if __name__ == "__main__":
    unittest.main()

