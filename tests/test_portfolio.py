import math
import unittest
from unittest.mock import MagicMock

from price_action_bot.config import Settings
from price_action_bot.engine import TradingEngine
from price_action_bot.models import Candle
from price_action_bot.portfolio import PortfolioParameters, build_portfolio_map


def candles_from_returns(returns: list[float], *, start: float = 100.0) -> list[Candle]:
    close = start
    rows = [Candle(0, close, close, close, close, 1000.0)]
    for index, value in enumerate(returns, start=1):
        previous = close
        close *= math.exp(value)
        rows.append(
            Candle(
                index * 3_600_000,
                previous,
                max(previous, close),
                min(previous, close),
                close,
                1000.0 + index,
            )
        )
    return rows


class PortfolioMapTests(unittest.TestCase):
    def setUp(self):
        self.parameters = PortfolioParameters(
            lookback_hours=48,
            min_overlap=20,
            healthy_overlap=36,
            cluster_correlation=0.70,
            risk_per_trade=0.01,
        )
        self.btc_returns = [
            0.003 * math.sin(index / 3) + 0.0015 * math.cos(index / 7)
            for index in range(48)
        ]

    def correlated_series(self) -> dict[str, list[Candle]]:
        return {
            "BTCUSDT": candles_from_returns(self.btc_returns, start=80_000),
            "ETHUSDT": candles_from_returns(
                [value * 1.15 + 0.0001 * math.sin(index) for index, value in enumerate(self.btc_returns)],
                start=2_500,
            ),
            "SOLUSDT": candles_from_returns(
                [value * 0.85 - 0.0001 * math.cos(index) for index, value in enumerate(self.btc_returns)],
                start=150,
            ),
        }

    def test_three_correlated_longs_are_one_shared_exposure(self):
        snapshot = build_portfolio_map(
            self.correlated_series(),
            active_exposures=[
                {"symbol": symbol, "side": "Buy", "state": "armed", "risk_fraction": 0.01}
                for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
            ],
            parameters=self.parameters,
            now_ms=123456,
        )

        shared = snapshot["effective_exposure"]["shared_directional_groups"]
        self.assertEqual(len(shared), 1)
        self.assertEqual(set(shared[0]["symbols"]), {"BTCUSDT", "ETHUSDT", "SOLUSDT"})
        self.assertEqual(shared[0]["position_count"], 3)
        self.assertAlmostEqual(shared[0]["effective_risk_fraction"], 0.03)
        self.assertAlmostEqual(snapshot["effective_exposure"]["gross_risk_fraction"], 0.03)

    def test_highest_quality_per_cluster_is_selected_without_risk_increase(self):
        snapshot = build_portfolio_map(
            self.correlated_series(),
            opportunities=[
                {"symbol": "BTCUSDT", "side": "Buy", "quality_score": 70},
                {"symbol": "ETHUSDT", "side": "Buy", "quality_score": 91},
                {"symbol": "SOLUSDT", "side": "Buy", "quality_score": 82},
            ],
            parameters=self.parameters,
            now_ms=123456,
        )

        selection = {row["symbol"]: row for row in snapshot["opportunity_selection"]}
        self.assertEqual(selection["ETHUSDT"]["decision"], "SELECTED")
        self.assertEqual(selection["ETHUSDT"]["recommended_risk_fraction"], 0.01)
        self.assertEqual(selection["BTCUSDT"]["decision"], "QUEUED_CORRELATED")
        self.assertEqual(selection["SOLUSDT"]["decision"], "QUEUED_CORRELATED")
        self.assertTrue(all(row["recommended_risk_fraction"] <= 0.01 for row in selection.values()))
        self.assertFalse(snapshot["risk_increase_authority"])
        self.assertFalse(snapshot["v2_execution_authority"])

    def test_relative_strength_is_ranked_inside_cluster(self):
        snapshot = build_portfolio_map(
            self.correlated_series(), parameters=self.parameters, now_ms=123456
        )
        correlated_cluster = next(cluster for cluster in snapshot["clusters"] if cluster["member_count"] == 3)
        ranks = {
            feature["symbol"]: feature["relative_strength_rank"]
            for feature in snapshot["features"]
        }
        self.assertEqual(
            correlated_cluster["relative_strength_order"],
            sorted(ranks, key=ranks.get),
        )
        self.assertEqual(sorted(ranks.values()), [1, 2, 3])

    def test_missing_and_weak_data_are_not_reported_as_confident(self):
        series = self.correlated_series()
        series["ETHUSDT"] = candles_from_returns(self.btc_returns[:25], start=2_500)
        series["SOLUSDT"] = candles_from_returns(self.btc_returns[:10], start=150)
        snapshot = build_portfolio_map(
            series,
            opportunities=[
                {"symbol": "BTCUSDT", "side": "Buy", "quality_score": 70},
                {"symbol": "SOLUSDT", "side": "Buy", "quality_score": 99},
            ],
            parameters=self.parameters,
            now_ms=123456,
            data_errors={"XRPUSDT": "TimeoutError"},
        )

        features = {item["symbol"]: item for item in snapshot["features"]}
        self.assertEqual(features["ETHUSDT"]["data_confidence"], "weak")
        self.assertEqual(features["SOLUSDT"]["data_confidence"], "insufficient")
        self.assertEqual(features["SOLUSDT"]["market_alignment"], "unknown")
        sol_selection = next(
            row for row in snapshot["opportunity_selection"] if row["symbol"] == "SOLUSDT"
        )
        self.assertEqual(sol_selection["decision"], "UNRESOLVED_DATA")
        self.assertEqual(sol_selection["recommended_risk_fraction"], 0.0)
        self.assertEqual(
            sol_selection["reason"], "weak_or_missing_direction_or_correlation_evidence"
        )
        self.assertEqual(snapshot["data_errors"], {"XRPUSDT": "TimeoutError"})

    def test_snapshot_is_deterministic_for_identical_inputs(self):
        first = build_portfolio_map(
            self.correlated_series(), parameters=self.parameters, now_ms=123456
        )
        second = build_portfolio_map(
            self.correlated_series(), parameters=self.parameters, now_ms=123456
        )
        self.assertEqual(first, second)

    def test_engine_records_shadow_snapshot_without_order_authority(self):
        gateway = MagicMock()
        series = self.correlated_series()
        gateway.candles.side_effect = lambda symbol, interval, limit: series[symbol]
        store = MagicMock()
        store.load_open_trades.return_value = {}
        store.expire_armed_signal_journal.return_value = 0
        store.market_snapshots.return_value = [
            {"symbol": "BTCUSDT", "bias": "bullish"},
            {"symbol": "ETHUSDT", "bias": "bullish"},
        ]
        settings = Settings(
            "demo-key",
            "demo-secret",
            v2_portfolio_mode="shadow",
            portfolio_lookback_hours=48,
            portfolio_min_overlap=20,
            portfolio_healthy_overlap=36,
            portfolio_cluster_correlation=0.70,
        )
        engine = TradingEngine(settings, gateway, store, MagicMock())
        scanner_snapshot = {
            "deep_analysis_pool": [{"symbol": "ETHUSDT"}, {"symbol": "SOLUSDT"}],
            "action_queue": [
                {"symbol": "ETHUSDT", "quality_score": 90},
                {"symbol": "SOLUSDT", "quality_score": 80},
            ],
        }

        engine._record_phase3_portfolio(scanner_snapshot, 2_000_000_000_000)

        saved = store.record_thesisedge_portfolio.call_args.args[0]
        self.assertEqual(gateway.candles.call_count, 3)
        self.assertFalse(saved["v2_execution_authority"])
        self.assertFalse(saved["risk_increase_authority"])
        self.assertEqual(
            next(row for row in saved["opportunity_selection"] if row["symbol"] == "ETHUSDT")["decision"],
            "SELECTED",
        )
        self.assertFalse(gateway.place_market_order.called)

    def test_engine_keeps_partial_snapshot_when_one_candle_request_fails(self):
        gateway = MagicMock()
        series = self.correlated_series()

        def load_candles(symbol, interval, limit):
            if symbol == "SOLUSDT":
                raise TimeoutError("history unavailable")
            return series[symbol]

        gateway.candles.side_effect = load_candles
        store = MagicMock()
        store.load_open_trades.return_value = {}
        store.expire_armed_signal_journal.return_value = 0
        store.market_snapshots.return_value = []
        settings = Settings(
            "demo-key",
            "demo-secret",
            v2_portfolio_mode="shadow",
            portfolio_lookback_hours=48,
            portfolio_min_overlap=20,
            portfolio_healthy_overlap=36,
        )
        engine = TradingEngine(settings, gateway, store, MagicMock())

        engine._record_phase3_portfolio(
            {
                "deep_analysis_pool": [{"symbol": "ETHUSDT"}, {"symbol": "SOLUSDT"}],
                "action_queue": [],
            },
            2_000_000_000_000,
        )

        saved = store.record_thesisedge_portfolio.call_args.args[0]
        self.assertEqual(saved["data_errors"], {"SOLUSDT": "TimeoutError"})
        self.assertEqual(saved["metrics"]["analyzed_symbols"], 2)
        self.assertFalse(gateway.place_market_order.called)


if __name__ == "__main__":
    unittest.main()
