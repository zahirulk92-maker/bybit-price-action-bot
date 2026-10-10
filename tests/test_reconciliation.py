import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from price_action_bot.config import Settings
from price_action_bot.engine import TradingEngine
from price_action_bot.models import ArmedSignal, PatternSignal, Trade


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

    def test_any_unknown_exchange_position_blocks_every_new_entry(self):
        self.gateway.open_positions.return_value = [
            {"symbol": "HYPEUSDT", "side": "Sell", "size": "42.36", "stopLoss": ""},
        ]
        pattern = PatternSignal(
            name="bullish_engulfing",
            side="Buy",
            timestamp_ms=1,
            trigger=100.0,
            stop=95.0,
            pattern_high=100.0,
            pattern_low=95.0,
            volume_ratio=1.5,
        )
        armed = ArmedSignal(pattern=pattern, armed_at_ms=1, expires_at_ms=2, target=115.0)
        self.engine._check_daily_loss_limit = MagicMock(
            return_value={"available": True, "breached": False}
        )

        self.engine._enter("BTCUSDT", armed, 100.0)

        self.gateway.place_market_order.assert_not_called()
        self.gateway.equity_usdt.assert_not_called()
        matching_events = [
            call for call in self.store.event.call_args_list
            if call.args and call.args[0] == "ENTRY_BLOCKED_RECONCILIATION"
        ]
        self.assertEqual(len(matching_events), 1)
        self.assertEqual(matching_events[0].args[1], "BTCUSDT")
        self.assertEqual(matching_events[0].kwargs["blocked_symbols"], ["HYPEUSDT"])

    def test_preflight_rejects_entry_after_price_leaves_trigger_band(self):
        pattern = PatternSignal(
            name="bullish_engulfing", side="Buy", timestamp_ms=1,
            trigger=100.0, stop=99.0, pattern_high=100.0, pattern_low=99.0,
            volume_ratio=1.5,
        )
        armed = ArmedSignal(pattern=pattern, armed_at_ms=1, expires_at_ms=2, target=104.0)

        self.engine._enter("BTCUSDT", armed, 99.99)

        self.gateway.place_market_order.assert_not_called()
        self.assertEqual(self.store.event.call_args.args[0], "ENTRY_BLOCKED_PREFLIGHT")

    def test_actual_fill_outside_boundary_is_closed_without_being_persisted(self):
        pattern = PatternSignal(
            name="bullish_engulfing", side="Buy", timestamp_ms=1,
            trigger=100.0, stop=99.0, pattern_high=100.0, pattern_low=99.0,
            volume_ratio=1.5,
        )
        armed = ArmedSignal(pattern=pattern, armed_at_ms=1, expires_at_ms=2, target=104.0)
        self.engine._check_daily_loss_limit = MagicMock(
            return_value={"available": True, "breached": False}
        )
        self.engine._reconcile_exchange_positions = MagicMock()
        self.gateway.equity_usdt.return_value = 1_000
        self.gateway.instrument_rules.return_value = SimpleNamespace(
            qty_step=0.001, min_qty=0.001, max_market_qty=1_000
        )
        self.gateway.place_market_order.return_value = "entry-1"
        self.gateway.wait_for_position.return_value = {"avgPrice": "100.03", "size": "9"}

        self.engine._enter("BTCUSDT", armed, 100.01)

        self.gateway.close_partial.assert_called_once_with("BTCUSDT", "Buy", 9.0)
        self.gateway.set_protection.assert_not_called()
        self.store.save_trade.assert_not_called()
        self.assertNotIn("BTCUSDT", self.engine.trades)
        event_names = [call.args[0] for call in self.store.event.call_args_list]
        self.assertIn("EMERGENCY_CLOSE_INVALID_FILL", event_names)

    def test_valid_entry_captures_decision_sizing_and_lifecycle(self):
        pattern = PatternSignal(
            name="bullish_engulfing", side="Buy", timestamp_ms=1,
            trigger=100.0, stop=99.0, pattern_high=100.0, pattern_low=99.0,
            volume_ratio=1.5,
        )
        armed = ArmedSignal(pattern=pattern, armed_at_ms=1, expires_at_ms=2, target=104.0)
        self.engine._check_daily_loss_limit = MagicMock(
            return_value={"available": True, "breached": False}
        )
        self.engine._reconcile_exchange_positions = MagicMock()
        self.engine.last_funnel_snapshot = {
            "schema_version": "thesisedge.demo-funnel.v1",
            "snapshot_id": "funnel-1",
            "computed_at_ms": 123,
            "candidate_pool": [{"symbol": "BTCUSDT", "bias_4h": "bullish"}],
            "deep_analysis_pool": [{"symbol": "BTCUSDT", "bias_1h": "bullish"}],
            "action_queue": [{"symbol": "BTCUSDT", "confirmation_15m": "fast_slow_momentum"}],
        }
        self.store.create_trade_audit.return_value = "ta-entry"
        self.gateway.equity_usdt.return_value = 1_000
        self.gateway.instrument_rules.return_value = SimpleNamespace(
            qty_step=0.001, min_qty=0.001, max_market_qty=1_000
        )
        self.gateway.place_market_order.return_value = "entry-1"
        self.gateway.wait_for_position.return_value = {"avgPrice": "100.01", "size": "9.9"}

        self.engine._enter(
            "BTCUSDT",
            armed,
            100.01,
            {"summary": "Entry confirmed", "checks": [{"key": "trigger", "status": "pass"}]},
        )

        snapshot = self.store.create_trade_audit.call_args.args[0]
        self.assertEqual(snapshot["decision"]["funnel"]["candidate_pool"]["bias_4h"], "bullish")
        self.assertEqual(snapshot["decision"]["funnel"]["deep_analysis_pool"]["bias_1h"], "bullish")
        self.assertEqual(snapshot["strategy"]["entry_timeframe"], "15m")
        self.assertEqual(snapshot["sizing"]["risk_budget_usdt"], 10.0)
        self.assertAlmostEqual(snapshot["sizing"]["expected_loss_at_stop_usdt"], 9.9 * 1.01)
        saved_trade = self.store.save_trade.call_args.args[0]
        self.assertEqual(saved_trade.trade_id, "ta-entry")
        lifecycle_names = [
            call.args[2] for call in self.store.record_trade_lifecycle_event.call_args_list
        ]
        self.assertEqual(
            lifecycle_names,
            [
                "SETUP_FOUND", "ARMED", "SETUP_CAPTURED",
                "ORDER_SUBMIT_REQUESTED", "ORDER_ACCEPTED", "FILL_CONFIRMED",
                "PROTECTION_SET", "POSITION_OPENED",
            ],
        )

    def test_reconcile_closes_stale_local_trade(self):
        trade = self.trade()
        self.engine.trades = {trade.symbol: trade}
        self.gateway.open_positions.return_value = []

        self.engine._reconcile_exchange_positions(force=True)

        self.assertNotIn("BTCUSDT", self.engine.trades)
        self.store.close_trade.assert_called_once_with(
            "BTCUSDT", "EXCHANGE_RECONCILIATION", management_state="POSITION_OPEN"
        )
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

    def test_closed_pnl_notification_is_exchange_confirmed_and_deduplicated(self):
        self.gateway.closed_pnl.return_value = [{
            "symbol": "BTCUSDT", "side": "Sell", "qty": "0.1",
            "avgEntryPrice": "100", "avgExitPrice": "110", "closedPnl": "0.95",
            "openFee": "-0.02", "closeFee": "-0.03", "updatedTime": "1000",
            "orderId": "exit-1",
        }]

        self.engine._notify_new_closed_pnl()
        self.engine.last_pnl_poll_ms = 0
        self.engine._notify_new_closed_pnl()

        self.assertEqual(self.notifier.send.call_count, 1)
        message = self.notifier.send.call_args.args[0]
        self.assertIn("BYBIT EXIT CONFIRMED", message)
        self.assertIn("+0.950000 USDT", message)
        matching_events = [
            call for call in self.store.event.call_args_list
            if call.args and call.args[0] == "EXCHANGE_PNL_CONFIRMED"
        ]
        self.assertEqual(len(matching_events), 1)

    def test_daily_loss_guard_locks_entries_at_five_percent(self):
        self.gateway.closed_pnl.return_value = [
            {"closedPnl": "-30"},
            {"closedPnl": "-20"},
        ]
        self.gateway.wallet_summary.return_value = {"wallet_balance": 950.0}

        result = self.engine._check_daily_loss_limit(force=True)

        self.assertTrue(result["available"])
        self.assertTrue(result["breached"])
        self.assertEqual(result["starting_capital"], 1000.0)
        self.assertEqual(result["max_loss_usdt"], 50.0)
        self.assertEqual(result["remaining_usdt"], 0.0)
        event_names = [call.args[0] for call in self.store.event.call_args_list]
        self.assertIn("DAILY_LOSS_LIMIT_REACHED", event_names)
        self.notifier.send.assert_called_once()

    def test_daily_loss_guard_allows_entries_below_limit(self):
        self.gateway.closed_pnl.return_value = [{"closedPnl": "-35"}]
        self.gateway.wallet_summary.return_value = {"wallet_balance": 965.0}

        result = self.engine._check_daily_loss_limit(force=True)

        self.assertTrue(result["available"])
        self.assertFalse(result["breached"])
        self.assertEqual(result["starting_capital"], 1000.0)
        self.assertEqual(result["remaining_usdt"], 15.0)
        self.notifier.send.assert_not_called()

    def test_daily_loss_guard_fails_closed_when_exchange_data_is_missing(self):
        self.gateway.closed_pnl.side_effect = TimeoutError("Bybit unavailable")

        result = self.engine._check_daily_loss_limit(force=True)

        self.assertFalse(result["available"])
        self.assertFalse(result["breached"])


if __name__ == "__main__":
    unittest.main()
