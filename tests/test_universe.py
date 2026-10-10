import unittest
import math
from unittest.mock import MagicMock

from price_action_bot.config import Settings
from price_action_bot.engine import TradingEngine
from price_action_bot.universe import (
    ScannerFunnelParameters,
    build_demo_timeframe_funnel,
    build_scanner_funnel,
    timeframe_confirmation,
)
from price_action_bot.models import Candle


class UniverseTests(unittest.TestCase):
    NOW = 2_000_000_000_000

    @classmethod
    def instrument(cls, symbol, *, age_days=60, status="Trading"):
        return {
            "symbol": symbol,
            "baseCoin": symbol.removesuffix("USDT"),
            "quoteCoin": "USDT",
            "status": status,
            "contractType": "LinearPerpetual",
            "launchTime": str(cls.NOW - age_days * 24 * 60 * 60 * 1000),
        }

    @staticmethod
    def ticker(
        symbol,
        *,
        turnover=10_000_000,
        open_interest=2_000_000,
        bid=100,
        ask=100.01,
        move=0.02,
    ):
        return {
            "symbol": symbol,
            "lastPrice": str((bid + ask) / 2),
            "bid1Price": str(bid),
            "ask1Price": str(ask),
            "turnover24h": str(turnover),
            "openInterestValue": str(open_interest),
            "price24hPcnt": str(move),
            "highPrice24h": str(ask * 1.05),
            "lowPrice24h": str(bid * 0.95),
        }

    @staticmethod
    def timeframe_candles(interval_ms, count=80, bullish=True):
        direction = 1 if bullish else -1
        rows = []
        for index in range(count):
            close = 100 + direction * index * 0.5 + direction * 2 * math.sin(index * math.pi / 3)
            rows.append(Candle(
                (index + 1) * interval_ms,
                close - direction * 0.2,
                close + 1,
                close - 1,
                close,
                1_000 + index,
            ))
        return rows

    def test_scanner_pool_is_capped_but_never_forced(self):
        symbols = ["AAAUSDT", "BBBUSDT"]
        snapshot = build_scanner_funnel(
            [self.ticker(symbol) for symbol in symbols],
            [self.instrument(symbol) for symbol in symbols],
            now_ms=self.NOW,
        )
        self.assertEqual(snapshot["metrics"]["candidate_count"], 2)
        self.assertEqual([row["symbol"] for row in snapshot["candidate_pool"]], symbols)
        self.assertEqual(snapshot["deep_analysis_pool"], [])
        self.assertFalse(snapshot["v2_execution_authority"])

    def test_scanner_records_filters_and_anomaly(self):
        instruments = [
            self.instrument("GOODUSDT"),
            self.instrument("NEWUSDT", age_days=2),
            self.instrument("THINUSDT"),
            self.instrument("WIDEUSDT"),
            self.instrument("MOVEUSDT"),
        ]
        tickers = [
            self.ticker("GOODUSDT"),
            self.ticker("NEWUSDT"),
            self.ticker("THINUSDT", turnover=10),
            self.ticker("WIDEUSDT", bid=100, ask=101),
            self.ticker("MOVEUSDT", move=0.30),
        ]
        snapshot = build_scanner_funnel(tickers, instruments, now_ms=self.NOW)
        exclusions = snapshot["metrics"]["exclusions"]
        candidates = {row["symbol"]: row for row in snapshot["candidate_pool"]}
        self.assertEqual(exclusions["listing_too_new"], 1)
        self.assertEqual(exclusions["turnover_below_floor"], 1)
        self.assertEqual(exclusions["spread_too_wide"], 1)
        self.assertTrue(candidates["MOVEUSDT"]["anomaly"])
        self.assertEqual(candidates["MOVEUSDT"]["volatility_state"], "abnormal")

    def test_scanner_hysteresis_requires_meaningful_newcomer_advantage(self):
        params = ScannerFunnelParameters(candidate_max=1, deep_analysis_max=1, action_queue_max=1)
        incumbent = build_scanner_funnel(
            [self.ticker("OLDUSDT", turnover=10_000_000)],
            [self.instrument("OLDUSDT")],
            parameters=params,
            now_ms=self.NOW,
        )
        marginal = build_scanner_funnel(
            [
                self.ticker("OLDUSDT", turnover=10_000_000),
                self.ticker("NEWUSDT", turnover=12_000_000),
            ],
            [self.instrument("OLDUSDT"), self.instrument("NEWUSDT")],
            parameters=params,
            previous_snapshot=incumbent,
            now_ms=self.NOW + 1,
        )
        self.assertEqual(marginal["candidate_pool"][0]["symbol"], "OLDUSDT")
        decisive = build_scanner_funnel(
            [
                self.ticker("OLDUSDT", turnover=10_000_000),
                self.ticker("NEWUSDT", turnover=1_000_000_000_000),
            ],
            [self.instrument("OLDUSDT"), self.instrument("NEWUSDT")],
            parameters=params,
            previous_snapshot=incumbent,
            now_ms=self.NOW + 2,
        )
        self.assertEqual(decisive["candidate_pool"][0]["symbol"], "NEWUSDT")
        self.assertFalse(decisive["metrics"]["churn_within_limit"])

    def test_scanner_exit_grace_prevents_one_refresh_churn(self):
        initial = build_scanner_funnel(
            [self.ticker("OLDUSDT")],
            [self.instrument("OLDUSDT")],
            now_ms=self.NOW,
        )
        first_miss = build_scanner_funnel(
            [],
            [self.instrument("OLDUSDT")],
            previous_snapshot=initial,
            now_ms=self.NOW + 1,
        )
        second_miss = build_scanner_funnel(
            [],
            [self.instrument("OLDUSDT")],
            previous_snapshot=first_miss,
            now_ms=self.NOW + 2,
        )
        removed = build_scanner_funnel(
            [],
            [self.instrument("OLDUSDT")],
            previous_snapshot=second_miss,
            now_ms=self.NOW + 3,
        )
        self.assertEqual(first_miss["candidate_pool"][0]["eligibility_state"], "exit_grace")
        self.assertEqual(second_miss["candidate_pool"][0]["missed_scans"], 2)
        self.assertEqual(first_miss["action_queue"], [])
        self.assertEqual(removed["candidate_pool"], [])

    def test_scanner_tracks_protected_symbols_and_prioritizes_near_zone(self):
        symbols = ["NEARUSDT", "FARUSDT"]
        snapshot = build_scanner_funnel(
            [self.ticker(symbol) for symbol in symbols],
            [self.instrument(symbol) for symbol in symbols],
            protected_symbols={"OPENUSDT"},
            market_locations={
                "NEARUSDT": {"support": 99.5, "resistance": 120},
                "FARUSDT": {"support": 70, "resistance": 130},
            },
            now_ms=self.NOW,
        )
        self.assertIn("OPENUSDT", snapshot["tracking_symbols"])
        self.assertEqual(snapshot["scan_schedule"][0]["tier"], "protected")
        self.assertEqual(snapshot["deep_analysis_pool"][0]["symbol"], "NEARUSDT")
        self.assertEqual(snapshot["action_queue"][0]["symbol"], "NEARUSDT")

    def test_scanner_snapshot_is_deterministic_for_fixed_inputs(self):
        args = (
            [self.ticker("BTCUSDT")],
            [self.instrument("BTCUSDT")],
        )
        first = build_scanner_funnel(*args, now_ms=self.NOW, elapsed_ms=12)
        second = build_scanner_funnel(*args, now_ms=self.NOW, elapsed_ms=12)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])

    def test_demo_funnel_replaces_the_old_v1_universe(self):
        symbols = ["BTCUSDT", "ETHUSDT"] + [f"COIN{i}USDT" for i in range(8)]
        tickers = [
            self.ticker(symbol, turnover=10_000_000 + index * 1_000_000)
            for index, symbol in enumerate(symbols)
        ]
        instruments = [self.instrument(symbol) for symbol in symbols]
        gateway = MagicMock()
        gateway.tickers.return_value = tickers
        gateway.instruments.return_value = instruments
        gateway.candles.side_effect = lambda symbol, interval, limit: self.timeframe_candles(
            int(interval) * 60_000, limit, bullish=True
        )
        store = MagicMock()
        store.load_open_trades.return_value = {}
        store.market_snapshots.return_value = []
        store.latest_thesisedge_universe.return_value = None
        engine = TradingEngine(
            Settings("demo-key", "demo-secret", v2_universe_mode="demo"),
            gateway,
            store,
            MagicMock(),
        )

        with unittest.mock.patch("price_action_bot.engine.time.time", return_value=self.NOW / 1000):
            engine.refresh_universe(force=True)

        self.assertEqual(len(engine.symbols), 10)
        self.assertEqual(set(engine.symbols), set(symbols))
        snapshot = store.record_thesisedge_universe.call_args.args[0]
        self.assertTrue(snapshot["v2_execution_authority"])
        self.assertEqual(snapshot["mode"], "demo")
        self.assertLessEqual(len(snapshot["candidate_pool"]), 20)
        self.assertLessEqual(len(snapshot["deep_analysis_pool"]), 10)
        self.assertEqual(
            {row["symbol"] for row in snapshot["action_queue"]},
            set(engine.funnel_entry_symbols),
        )
        self.assertEqual(len(snapshot["action_queue"]), 10)

        store.event.reset_mock()
        store.record_thesisedge_universe.reset_mock()
        with unittest.mock.patch(
            "price_action_bot.engine.time.time", return_value=(self.NOW + 901_000) / 1000
        ):
            engine.refresh_universe(force=False)

        self.assertEqual(set(engine.symbols), set(symbols))
        store.record_thesisedge_universe.assert_called_once()
        event_names = [call.args[0] for call in store.event.call_args_list]
        self.assertIn("DEMO_TIMEFRAME_FUNNEL_UPDATED", event_names)

        store.event.reset_mock()
        gateway.tickers.side_effect = TimeoutError("scanner-only request failed")
        with unittest.mock.patch(
            "price_action_bot.engine.time.time", return_value=(self.NOW + 1_802_000) / 1000
        ):
            engine.refresh_universe(force=False)

        self.assertEqual(set(engine.symbols), set(symbols))
        self.assertEqual(store.event.call_args.args[0], "DEMO_TIMEFRAME_FUNNEL_ERROR")

    def test_15m_confirmation_is_directional_and_closed_candle_based(self):
        bullish = self.timeframe_candles(900_000, bullish=True)
        bearish = self.timeframe_candles(900_000, bullish=False)
        self.assertTrue(timeframe_confirmation(bullish, "Buy")[0])
        self.assertFalse(timeframe_confirmation(bullish, "Sell")[0])
        self.assertTrue(timeframe_confirmation(bearish, "Sell")[0])
        self.assertFalse(timeframe_confirmation(bearish, "Buy")[0])

    def test_demo_funnel_requires_4h_1h_and_15m_alignment(self):
        symbols = ["ALIGNEDUSDT", "CONFLICTUSDT"]
        params = ScannerFunnelParameters(candidate_max=20, deep_analysis_max=10, action_queue_max=10)
        bullish_4h = self.timeframe_candles(14_400_000, bullish=True)
        bullish_1h = self.timeframe_candles(3_600_000, bullish=True)
        snapshot = build_demo_timeframe_funnel(
            [self.ticker(symbol) for symbol in symbols],
            [self.instrument(symbol) for symbol in symbols],
            candles_4h={symbol: bullish_4h for symbol in symbols},
            candles_1h={symbol: bullish_1h for symbol in symbols},
            candles_15m={
                "ALIGNEDUSDT": self.timeframe_candles(900_000, bullish=True),
                "CONFLICTUSDT": self.timeframe_candles(900_000, bullish=False),
            },
            parameters=params,
            now_ms=self.NOW,
        )
        self.assertEqual(len(snapshot["candidate_pool"]), 2)
        self.assertEqual(len(snapshot["deep_analysis_pool"]), 2)
        self.assertEqual([row["symbol"] for row in snapshot["action_queue"]], ["ALIGNEDUSDT"])
        self.assertTrue(snapshot["v2_execution_authority"])

    def test_demo_funnel_does_not_report_shadow_migration_as_churn(self):
        symbol = "ALIGNEDUSDT"
        snapshot = build_demo_timeframe_funnel(
            [self.ticker(symbol)],
            [self.instrument(symbol)],
            candles_4h={symbol: self.timeframe_candles(14_400_000, bullish=True)},
            candles_1h={symbol: self.timeframe_candles(3_600_000, bullish=True)},
            candles_15m={symbol: self.timeframe_candles(900_000, bullish=True)},
            previous_snapshot={
                "schema_version": "thesisedge.phase2.v1",
                "candidate_pool": [{"symbol": "OLDUSDT"}],
            },
            now_ms=self.NOW,
        )
        self.assertEqual(snapshot["metrics"]["churn_rate"], 0.0)
        self.assertTrue(snapshot["metrics"]["churn_within_limit"])


if __name__ == "__main__":
    unittest.main()

