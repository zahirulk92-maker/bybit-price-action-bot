import tempfile
import unittest
from pathlib import Path

from price_action_bot.models import SignalState, Trade
from price_action_bot.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.store = Store(str(Path(self.temp_dir.name) / "test.db"))

    def tearDown(self):
        self.store.connection.close()
        self.temp_dir.cleanup()

    def test_dashboard_state_round_trip(self):
        self.store.market_snapshot("BTCUSDT", 100, "bullish", 95, 110, "SCAN")
        self.store.heartbeat("running", mode="SIGNAL ONLY")
        self.store.set_trading_enabled(False)
        self.assertFalse(self.store.trading_enabled())
        self.assertEqual(self.store.market_snapshots()[0]["symbol"], "BTCUSDT")
        self.assertEqual(self.store.get_heartbeat()["status"], "running")

    def test_trade_history_is_not_overwritten_after_close(self):
        first = Trade("BTCUSDT", "Buy", 0.01, 100, 98, 104, 102)
        self.store.save_trade(first)
        self.store.close_trade("BTCUSDT")
        second = Trade("BTCUSDT", "Buy", 0.02, 105, 103, 110, 107, SignalState.POSITION_OPEN)
        self.store.save_trade(second)
        history = self.store.recent_trades()
        self.assertEqual(len(history), 2)
        self.assertEqual(sum(row["status"] == "open" for row in history), 1)


if __name__ == "__main__":
    unittest.main()
