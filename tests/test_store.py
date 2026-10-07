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
        self.store.close_trade("BTCUSDT", "REVERSAL_EXIT", pattern="bearish_engulfing")
        second = Trade("BTCUSDT", "Buy", 0.02, 105, 103, 110, 107, SignalState.POSITION_OPEN)
        self.store.save_trade(second)
        history = self.store.recent_trades()
        self.assertEqual(len(history), 2)
        self.assertEqual(sum(row["status"] == "open" for row in history), 1)
        closed = next(row for row in history if row["status"] == "closed")
        self.assertEqual(closed["trade"]["close_reason"], "REVERSAL_EXIT")
        self.assertEqual(closed["trade"]["close_details"]["pattern"], "bearish_engulfing")

    def test_decision_checklist_round_trip(self):
        decision = {
            "summary": "Waiting for volume",
            "checks": [
                {"key": "volume", "label": "Volume", "status": "wait", "detail": "0.9x"}
            ],
        }
        self.store.decision_snapshot("BTCUSDT", decision)
        saved = self.store.decision_snapshots()[0]
        self.assertEqual(saved["symbol"], "BTCUSDT")
        self.assertEqual(saved["checks"][0]["status"], "wait")
        self.assertGreater(saved["updated_at_ms"], 0)

    def test_signal_journal_tracks_outcome(self):
        signal_id = self.store.create_signal_journal(
            "BTCUSDT", "ARMED", pattern="bullish_engulfing", side="Buy", trigger=101.0
        )
        self.store.update_signal_journal(signal_id, "BTCUSDT", "SIGNAL_ONLY", entry=101.2, reason="Trigger confirmed")
        saved = self.store.recent_signal_journal()[0]
        self.assertEqual(saved["status"], "SIGNAL_ONLY")
        self.assertEqual(saved["pattern"], "bullish_engulfing")
        self.assertEqual(saved["entry"], 101.2)

    def test_worker_restart_expires_orphaned_armed_signals(self):
        self.store.create_signal_journal(
            "BTCUSDT", "ARMED", pattern="bullish_engulfing", side="Buy"
        )
        self.store.create_signal_journal(
            "ETHUSDT", "EXPIRED", pattern="bearish_pin_bar", side="Sell"
        )

        changed = self.store.expire_armed_signal_journal("Worker restarted")
        rows = self.store.recent_signal_journal()

        self.assertEqual(changed, 1)
        btc = next(row for row in rows if row["symbol"] == "BTCUSDT")
        eth = next(row for row in rows if row["symbol"] == "ETHUSDT")
        self.assertEqual(btc["status"], "EXPIRED_RESTART")
        self.assertEqual(btc["reason"], "Worker restarted")
        self.assertEqual(eth["status"], "EXPIRED")

    def test_phase2_scanner_snapshot_round_trip_is_deduplicated(self):
        snapshot = {
            "snapshot_id": "scanner-1",
            "computed_at_ms": 123456,
            "schema_version": "thesisedge.phase2.v1",
            "metrics": {"candidate_count": 1},
            "candidate_pool": [{"symbol": "BTCUSDT"}],
            "deep_analysis_pool": [],
            "action_queue": [],
            "tracking_symbols": ["BTCUSDT"],
            "v2_execution_authority": False,
        }
        self.store.record_thesisedge_universe(snapshot)
        self.store.record_thesisedge_universe(snapshot)
        saved = self.store.latest_thesisedge_universe()
        count = self.store._execute(
            "SELECT COUNT(*) AS total FROM thesisedge_universe_snapshots", fetch="one"
        )
        self.assertEqual(saved["candidate_pool"][0]["symbol"], "BTCUSDT")
        self.assertEqual(saved["mode"], "shadow")
        self.assertFalse(saved["v2_execution_authority"])
        self.assertEqual(dict(count)["total"], 1)

    def test_phase3_portfolio_snapshot_round_trip_is_deduplicated(self):
        snapshot = {
            "snapshot_id": "portfolio-1",
            "computed_at_ms": 123456,
            "schema_version": "thesisedge.phase3.v1",
            "metrics": {"cluster_count": 1},
            "clusters": [{"cluster_id": "cluster-01", "members": ["BTCUSDT"]}],
            "effective_exposure": {"gross_risk_fraction": 0.01},
            "v2_execution_authority": False,
            "risk_increase_authority": False,
        }
        self.store.record_thesisedge_portfolio(snapshot)
        self.store.record_thesisedge_portfolio(snapshot)

        saved = self.store.latest_thesisedge_portfolio()
        count = self.store._execute(
            "SELECT COUNT(*) AS total FROM thesisedge_portfolio_snapshots", fetch="one"
        )

        self.assertEqual(saved["clusters"][0]["members"], ["BTCUSDT"])
        self.assertEqual(saved["mode"], "shadow")
        self.assertFalse(saved["risk_increase_authority"])
        self.assertEqual(dict(count)["total"], 1)


if __name__ == "__main__":
    unittest.main()
