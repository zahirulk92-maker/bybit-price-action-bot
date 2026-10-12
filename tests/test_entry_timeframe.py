import unittest
from unittest.mock import MagicMock, patch

from price_action_bot.config import Settings
from price_action_bot.engine import ENTRY_TIMEFRAME_MS, TradingEngine
from price_action_bot.models import Candle, MarketContext, PatternSignal, Zone


class EntryTimeframeTests(unittest.TestCase):
    def test_engine_arms_from_closed_15m_candles_without_requesting_5m_entry_data(self):
        settings = Settings(
            api_key="demo-key",
            api_secret="demo-secret",
            enable_order_placement=False,
            v2_instrumentation_enabled=False,
            v2_structure_mode="off",
            v2_playbook_mode="off",
        )
        gateway = MagicMock()
        candles_15m = [
            Candle(index * 900_000, 100, 101, 99, 100.5, 100)
            for index in range(30)
        ]
        candles_1h = [
            Candle(index * 3_600_000, 100, 102, 98, 101, 1_000)
            for index in range(60)
        ]
        gateway.candles.side_effect = lambda symbol, interval, limit: (
            candles_15m if interval == "15" else candles_1h
        )
        store = MagicMock()
        store.load_open_trades.return_value = {}
        engine = TradingEngine(settings, gateway, store, MagicMock())
        engine.funnel_directions = {"BTCUSDT": "Buy"}
        # A stale funnel action queue must not duplicate the live 15m gate.
        engine.funnel_entry_symbols = set()
        context = MarketContext(
            bias="bullish",
            support=Zone(100, 99, 101, "support"),
            resistance=Zone(104, 103, 105, "resistance"),
            atr=2,
        )
        pattern = PatternSignal(
            name="bullish_engulfing",
            side="Buy",
            timestamp_ms=candles_15m[-1].timestamp_ms,
            trigger=100,
            stop=99,
            pattern_high=100,
            pattern_low=99,
            volume_ratio=1.5,
        )

        with (
            patch("price_action_bot.engine.market_context", return_value=context),
            patch("price_action_bot.engine.timeframe_confirmation", return_value=(True, 1.0)),
            patch("price_action_bot.engine.detect_pattern", return_value=pattern),
        ):
            engine._process_symbol("BTCUSDT", 100)

        requested_intervals = [call.args[1] for call in gateway.candles.call_args_list]
        self.assertEqual(requested_intervals, ["15", "60"])
        self.assertIn("BTCUSDT", engine.armed)
        self.assertEqual(
            engine.armed["BTCUSDT"].expires_at_ms,
            pattern.timestamp_ms + 4 * ENTRY_TIMEFRAME_MS,
        )

    def test_engine_rejects_15m_signal_when_current_1h_direction_conflicts(self):
        settings = Settings(
            api_key="demo-key",
            api_secret="demo-secret",
            enable_order_placement=False,
            v2_instrumentation_enabled=False,
            v2_structure_mode="off",
            v2_playbook_mode="off",
        )
        candles_15m = [
            Candle(index * 900_000, 100, 101, 99, 100.5, 100)
            for index in range(30)
        ]
        candles_1h = [
            Candle(index * 3_600_000, 100, 102, 98, 99, 1_000)
            for index in range(60)
        ]
        gateway = MagicMock()
        gateway.candles.side_effect = lambda symbol, interval, limit: (
            candles_15m if interval == "15" else candles_1h
        )
        store = MagicMock()
        store.load_open_trades.return_value = {}
        engine = TradingEngine(settings, gateway, store, MagicMock())
        engine.funnel_directions = {"BTCUSDT": "Buy"}
        bearish_context = MarketContext(
            bias="bearish",
            support=Zone(95, 94, 96, "support"),
            resistance=Zone(100, 99, 101, "resistance"),
            atr=2,
        )

        with (
            patch("price_action_bot.engine.market_context", return_value=bearish_context),
            patch("price_action_bot.engine.timeframe_confirmation", return_value=(True, 1.0)),
            patch("price_action_bot.engine.detect_pattern") as detect,
        ):
            engine._process_symbol("BTCUSDT", 100)

        detect.assert_not_called()
        self.assertNotIn("BTCUSDT", engine.armed)
        decision = store.decision_snapshot.call_args.args[1]
        self.assertIn("waiting for current 1h direction", decision["summary"])


if __name__ == "__main__":
    unittest.main()
