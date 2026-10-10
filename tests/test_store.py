import tempfile
import unittest
import sqlite3
from pathlib import Path

from price_action_bot.models import Candle, SignalState, Trade
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

    def test_trade_audit_snapshot_is_immutable_and_lifecycle_is_append_only(self):
        snapshot = {
            "schema_version": "thesisedge.trade-audit.v1",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "decision": {"pattern": "bullish_engulfing"},
            "sizing": {"risk_budget_usdt": 10.0, "final_quantity": 2.0},
        }
        trade_id = self.store.create_trade_audit(snapshot, "ta-fixed")
        changed = {**snapshot, "sizing": {"risk_budget_usdt": 999.0}}
        self.store.create_trade_audit(changed, "ta-fixed")
        self.store.record_trade_lifecycle_event(
            trade_id, "BTCUSDT", "ORDER_ACCEPTED", order_id="entry-1"
        )
        trade = Trade(
            "BTCUSDT", "Buy", 2.0, 100, 95, 115, 105,
            order_id="entry-1", trade_id=trade_id,
        )
        self.store.save_trade(trade)
        self.store.close_trade("BTCUSDT", "TP3_COMPLETE", price=115)

        audit = self.store.trade_audit(trade_id)

        self.assertIsNotNone(audit)
        self.assertEqual(audit["snapshot"]["sizing"]["risk_budget_usdt"], 10.0)
        self.assertEqual(audit["status"], "CLOSED")
        self.assertGreater(audit["closed_at_ms"], 0)
        self.assertEqual(
            [event["event_type"] for event in audit["events"]],
            ["ORDER_ACCEPTED", "CLOSED_LOCAL"],
        )
        self.assertEqual(audit["events"][1]["payload"]["reason"], "TP3_COMPLETE")

    def test_exchange_exit_matches_fill_and_is_deduplicated(self):
        snapshot = {
            "symbol": "BTCUSDT", "side": "Buy", "decision": {}, "sizing": {},
        }
        trade_id = self.store.create_trade_audit(snapshot, "ta-match")
        opened_at_ms = self.store.trade_audit(trade_id)["opened_at_ms"]
        self.store.record_trade_lifecycle_event(
            trade_id, "BTCUSDT", "FILL_CONFIRMED", actual_entry=100.0
        )

        matched = self.store.match_trade_audit("BTCUSDT", 100.1, opened_at_ms + 1)
        for _ in range(2):
            self.store.record_trade_lifecycle_event(
                matched,
                "BTCUSDT",
                "EXCHANGE_EXIT_CONFIRMED",
                external_event_id="bybit-closed-pnl:exit-1",
                closed_pnl=1.0,
            )

        audit = self.store.trade_audit(trade_id)
        exit_events = [
            event for event in audit["events"]
            if event["event_type"] == "EXCHANGE_EXIT_CONFIRMED"
        ]
        self.assertEqual(matched, trade_id)
        self.assertEqual(len(exit_events), 1)

    def test_recent_trade_audits_filters_and_summarizes_evidence(self):
        btc_id = self.store.create_trade_audit(
            {
                "symbol": "BTCUSDT", "side": "Buy", "decision": {}, "sizing": {},
            },
            "ta-btc",
        )
        self.store.record_trade_lifecycle_event(btc_id, "BTCUSDT", "SETUP_FOUND")
        self.store.archive_trade_candles(
            btc_id,
            "BTCUSDT",
            "5m",
            [Candle(1_000, 100, 101, 99, 100.5, 5)],
        )
        self.store.create_trade_audit(
            {
                "symbol": "ETHUSDT", "side": "Sell", "decision": {}, "sizing": {},
            },
            "ta-eth",
        )

        rows = self.store.recent_trade_audits(symbol="BTCUSDT")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_id"], "ta-btc")
        self.assertEqual(rows[0]["snapshot"]["side"], "Buy")
        self.assertEqual(rows[0]["event_count"], 1)
        self.assertEqual(rows[0]["captured_candle_count"], 1)
        self.assertFalse(rows[0]["excursion_available"])

    def test_buy_excursion_reports_pre_stop_peak_and_adverse_move(self):
        snapshot = {
            "symbol": "BTCUSDT", "side": "Buy", "decision": {}, "sizing": {},
            "strategy": {"stop": 95.0, "target": 110.0},
        }
        trade_id = self.store.create_trade_audit(snapshot, "ta-buy-path")
        self.store.record_trade_lifecycle_event(
            trade_id,
            "BTCUSDT",
            "FILL_CONFIRMED",
            occurred_at_ms=1_000,
            actual_entry=100.0,
        )
        self.store.archive_trade_candles(
            trade_id,
            "BTCUSDT",
            "5m",
            [
                Candle(1_000, 100, 106, 98, 104, 10),
                Candle(2_000, 104, 105, 94, 95, 12),
            ],
        )

        result = self.store.finalize_trade_excursion(
            trade_id, closed_at_ms=3_000, exit_price=95.0
        )

        self.assertEqual(result["captured_candle_count"], 2)
        self.assertEqual(len(self.store.trade_candles(trade_id)), 2)
        self.assertEqual(result["mfe_price"], 106.0)
        self.assertEqual(result["mae_price"], 94.0)
        self.assertAlmostEqual(result["mfe_r"], 1.2)
        self.assertAlmostEqual(result["mae_r"], 1.2)
        self.assertAlmostEqual(result["target_progress_percent"], 60.0)
        self.assertEqual(
            self.store.trade_audit(trade_id)["excursion"]["data_quality"],
            "CLOSED_CANDLE_PATH",
        )

    def test_sell_excursion_is_directionally_symmetric(self):
        snapshot = {
            "symbol": "ETHUSDT", "side": "Sell", "decision": {}, "sizing": {},
            "strategy": {"stop": 105.0, "target": 90.0},
        }
        trade_id = self.store.create_trade_audit(snapshot, "ta-sell-path")
        self.store.record_trade_lifecycle_event(
            trade_id,
            "ETHUSDT",
            "FILL_CONFIRMED",
            occurred_at_ms=1_000,
            actual_entry=100.0,
        )
        self.store.archive_trade_candles(
            trade_id,
            "ETHUSDT",
            "5m",
            [Candle(1_000, 100, 103, 94, 96, 10)],
        )

        result = self.store.finalize_trade_excursion(
            trade_id, closed_at_ms=2_000, exit_price=96.0
        )

        self.assertEqual(result["mfe_price"], 94.0)
        self.assertEqual(result["mae_price"], 103.0)
        self.assertAlmostEqual(result["mfe_r"], 1.2)
        self.assertAlmostEqual(result["mae_r"], 0.6)

    def test_additive_migration_upgrades_early_lifecycle_table(self):
        path = Path(self.temp_dir.name) / "legacy-audit.db"
        connection = sqlite3.connect(path)
        connection.execute(
            """CREATE TABLE trade_lifecycle_events (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT NOT NULL,
                   symbol TEXT NOT NULL, event_type TEXT NOT NULL,
                   occurred_at_ms BIGINT NOT NULL, payload TEXT NOT NULL
               )"""
        )
        connection.commit()
        connection.close()

        migrated = Store(str(path))
        columns = migrated._execute(
            "PRAGMA table_info(trade_lifecycle_events)", fetch="all"
        )
        migrated.connection.close()

        self.assertIn("external_event_id", {dict(row)["name"] for row in columns})

    def test_trade_id_survives_worker_restart_round_trip(self):
        trade = Trade(
            "ETHUSDT", "Sell", 1.0, 100, 105, 90, 95,
            trade_id="ta-restart",
        )
        self.store.save_trade(trade)

        loaded = self.store.load_open_trades()["ETHUSDT"]

        self.assertEqual(loaded.trade_id, "ta-restart")

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

    def test_phase4_playbook_latest_per_symbol_is_deduplicated(self):
        base = {
            "snapshot_id": "playbook-btc-1", "symbol": "BTCUSDT",
            "computed_at_ms": 123456, "schema_version": "thesisedge.phase4.v1",
            "status": "NO_MATCHING_PLAYBOOK", "v2_execution_authority": False,
        }
        self.store.record_thesisedge_playbook(base)
        self.store.record_thesisedge_playbook(base)
        later = {**base, "snapshot_id": "playbook-btc-2", "computed_at_ms": 123457, "status": "MATCHED"}
        eth = {**base, "snapshot_id": "playbook-eth-1", "symbol": "ETHUSDT"}
        self.store.record_thesisedge_playbook(later)
        self.store.record_thesisedge_playbook(eth)

        saved = self.store.latest_thesisedge_playbooks()

        self.assertEqual(len(saved), 2)
        self.assertEqual(next(row for row in saved if row["symbol"] == "BTCUSDT")["status"], "MATCHED")
        self.assertTrue(all(row["mode"] == "shadow" for row in saved))


if __name__ == "__main__":
    unittest.main()
