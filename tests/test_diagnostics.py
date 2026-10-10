import copy
import unittest

from price_action_bot.diagnostics import build_trade_diagnosis_report, diagnose_trade


def captured_audit(*, pnl=-2.0, mfe_r=0.4, expected_loss=9.5, actual_entry=100.05):
    return {
        "trade_id": "ta-1",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "status": "CLOSED",
        "closed_at_ms": 2_000,
        "snapshot": {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "strategy": {
                "direction_timeframes": ["4h", "1h"],
                "entry_timeframe": "15m",
                "management_timeframe": "5m",
                "trigger": 100.0,
                "stop": 90.0,
                "target": 122.0,
                "expected_net_reward_risk": 2.1,
                "pattern": "bullish_engulfing",
            },
            "decision": {
                "funnel": {
                    "candidate_pool": {"side": "Buy", "bias_4h": "bullish"},
                    "deep_analysis_pool": {
                        "side": "Buy", "bias_4h": "bullish", "bias_1h": "bullish",
                    },
                    "action_queue": {
                        "side": "Buy", "confirmation_15m": "fast_slow_momentum",
                    },
                },
            },
            "sizing": {
                "risk_budget_usdt": 10.0,
                "expected_loss_at_stop_usdt": expected_loss,
                "estimated_stop_fees_usdt": 0.2,
                "final_quantity": 1.0,
                "quantity_step": 0.01,
                "slippage_rate": 0.001,
            },
        },
        "events": [
            {
                "event_type": "FILL_CONFIRMED",
                "occurred_at_ms": 1_000,
                "payload": {
                    "actual_entry": actual_entry,
                    "actual_quantity": 1.0,
                    "net_reward_risk": 2.05,
                },
            },
            {
                "event_type": "CLOSED_LOCAL",
                "occurred_at_ms": 2_000,
                "payload": {"reason": "STOP_LOSS"},
            },
            {
                "event_type": "EXCHANGE_EXIT_CONFIRMED",
                "occurred_at_ms": 2_000,
                "payload": {"closed_pnl": pnl},
            },
        ],
        "excursion": {
            "data_quality": "CLOSED_CANDLE_PATH",
            "mfe_r": mfe_r,
            "mae_r": 1.0,
        },
    }


class TradeDiagnosisTests(unittest.TestCase):
    def test_valid_loss_when_rules_held_and_price_never_reached_one_r(self):
        result = diagnose_trade(captured_audit())

        self.assertEqual(result["verdict"], "VALID_LOSS")
        self.assertEqual(result["confidence"], "complete")
        self.assertEqual(result["issue_codes"], [])

    def test_exit_issue_when_one_r_profit_was_not_protected(self):
        result = diagnose_trade(captured_audit(mfe_r=1.25))

        self.assertEqual(result["verdict"], "EXIT_ISSUE")
        self.assertIn("PROFIT_NOT_PROTECTED_AFTER_1R", result["issue_codes"])

    def test_risk_issue_has_priority_over_trade_outcome(self):
        result = diagnose_trade(captured_audit(pnl=4.0, expected_loss=10.5))

        self.assertEqual(result["verdict"], "RISK_ISSUE")
        self.assertIn("PRICE_RISK_OVER_BUDGET", result["issue_codes"])

    def test_execution_issue_detects_chased_fill(self):
        result = diagnose_trade(captured_audit(actual_entry=100.2))

        self.assertEqual(result["verdict"], "EXECUTION_ISSUE")
        self.assertIn("ENTRY_OUTSIDE_CAPTURED_BOUNDARY", result["issue_codes"])

    def test_incomplete_capture_never_invents_a_verdict(self):
        result = diagnose_trade({"trade_id": "legacy", "events": []})

        self.assertEqual(result["verdict"], "INSUFFICIENT_DATA")
        self.assertEqual(result["authority"], "diagnostic_only")

    def test_report_ranks_recurring_issues_and_enforces_sample_floor(self):
        audits = [
            captured_audit(),
            captured_audit(pnl=3.0),
            captured_audit(mfe_r=1.2),
            captured_audit(mfe_r=1.5),
            captured_audit(pnl=3.0, expected_loss=10.5),
        ]
        for index, audit in enumerate(audits, start=1):
            audit["trade_id"] = f"ta-{index}"
        legacy = {"trade_id": "legacy", "symbol": "ETHUSDT", "events": []}

        report = build_trade_diagnosis_report([*audits, legacy])

        self.assertTrue(report["read_only"])
        self.assertEqual(report["authority"], "diagnostic_only")
        self.assertEqual(report["summary"]["conclusive_trades"], 5)
        self.assertEqual(report["summary"]["insufficient_data_trades"], 1)
        btc = next(row for row in report["by_symbol"] if row["key"] == "BTCUSDT")
        self.assertEqual(btc["sample_status"], "sufficient")
        self.assertEqual(btc["diagnosed_issue_rate"], 3 / 5)
        eth = next(row for row in report["by_symbol"] if row["key"] == "ETHUSDT")
        self.assertIsNone(eth["diagnosed_issue_rate"])
        self.assertEqual(report["recurring_issues"][0]["issue_code"], "PROFIT_NOT_PROTECTED_AFTER_1R")
        self.assertTrue(report["recurring_issues"][0]["recurring"])

    def test_report_does_not_mislabel_pattern_as_playbook(self):
        pattern_audit = captured_audit()
        playbook_audit = copy.deepcopy(pattern_audit)
        playbook_audit["trade_id"] = "ta-playbook"
        playbook_audit["snapshot"]["decision"]["playbook"] = {
            "selected_playbook": {"playbook": "BREAKOUT_RETEST"},
        }

        report = build_trade_diagnosis_report([pattern_audit, playbook_audit])

        groups = {(row["group_kind"], row["key"]) for row in report["by_strategy"]}
        self.assertIn(("pattern", "bullish_engulfing"), groups)
        self.assertIn(("playbook", "BREAKOUT_RETEST"), groups)


if __name__ == "__main__":
    unittest.main()
