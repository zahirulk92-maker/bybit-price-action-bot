import unittest
from unittest.mock import MagicMock

from price_action_bot.config import Settings
from price_action_bot.engine import TradingEngine
from price_action_bot.models import Trade


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            api_key="demo-key",
            api_secret="demo-secret",
            demo=True,
            enable_order_placement=True,
        )
        self.gateway = MagicMock()
        self.store = MagicMock()
        self.store.load_open_trades.return_value = {}
        self.notifier = MagicMock()
        self.engine = TradingEngine(self.settings, self.gateway, self.store, self.notifier)

    @staticmethod
    def trade(symbol: str = "BTCUSDT") -> Trade:
        return Trade(
            symbol=symbol,
            side="Buy",
            qty=1.0,
            entry=100.0,
            stop=95.0,
            target=115.0,
            one_r_target=105.0,
        )

    def test_reconcile_syncs_tracked_position_and_blocks_unknown_position(self):
        trade = self.trade()
        self.engine.trades = {trade.symbol: trade}
        self.gateway.open_positions.return_value = [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "1.2", "stopLoss": "96"},
            {"symbol": "ETHUSDT", "side": "Sell", "size": "0.4", "stopLoss": ""},
        ]

        self.engine._reconcile_exchange_positions(force=True)

        self.assertEqual(trade.qty, 1.2)
        self.assertEqual(trade.stop, 96.0)
        self.assertEqual(self.engine.blocked_symbols, {"ETHUSDT"})
        self.assertEqual(self.engine.reconciliation["status"], "attention")
        self.store.save_trade.assert_called_once_with(trade)

    def test_reconcile_closes_stale_local_trade(self):
        trade = self.trade()
        self.engine.trades = {trade.symbol: trade}
        self.gateway.open_positions.return_value = []

        self.engine._reconcile_exchange_positions(force=True)

        self.assertNotIn("BTCUSDT", self.engine.trades)
        self.store.close_trade.assert_called_once_with("BTCUSDT")
        event_names = [call.args[0] for call in self.store.event.call_args_list]
        self.assertIn("RECONCILED_LOCAL_CLOSED", event_names)

    def test_partial_trade_keeps_original_quantity(self):
        trade = self.trade()
        trade.partial_taken = True
        self.engine.trades = {trade.symbol: trade}
        self.gateway.open_positions.return_value = [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.5", "stopLoss": "95"},
        ]

        self.engine._reconcile_exchange_positions(force=True)

        self.assertEqual(trade.qty, 1.0)


if __name__ == "__main__":
    unittest.main()
