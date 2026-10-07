import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from price_action_bot.config import Settings
from price_action_bot.engine import TradingEngine
from price_action_bot.management import (
    RecoveryPolicy,
    break_even_with_costs,
    build_recovery_plan,
)
from price_action_bot.models import ArmedSignal, PatternSignal


class RecoveryManagementTests(unittest.TestCase):
    def test_fee_aware_quantity_keeps_total_stop_loss_inside_one_percent(self):
        plan = build_recovery_plan(
            equity=1_000,
            risk_fraction=0.01,
            entry=100,
            stop=99,
            obstacle=104,
            side="Buy",
            qty_step=0.001,
            min_qty=0.001,
            max_qty=1_000,
        )

        self.assertTrue(plan.accepted)
        self.assertLessEqual(plan.estimated_stop_loss, 10.0)
        self.assertGreater(plan.quantity, 0)

    def test_tp1_is_at_least_one_point_five_r_and_recovers_net_one_r(self):
        policy = RecoveryPolicy(taker_fee_rate=0.00055, slippage_rate=0.00020)
        plan = build_recovery_plan(
            equity=1_000,
            risk_fraction=0.01,
            entry=100,
            stop=99,
            obstacle=104,
            side="Buy",
            qty_step=0.001,
            min_qty=0.001,
            max_qty=1_000,
            policy=policy,
        )

        self.assertGreaterEqual(plan.tp1, 101.5)
        closed_qty = plan.quantity * policy.tp1_fraction
        modeled_net = (
            closed_qty * (plan.tp1 - 100)
            - plan.quantity * 100 * policy.taker_fee_rate
            - closed_qty * plan.tp1 * (policy.taker_fee_rate + policy.slippage_rate)
        )
        self.assertGreaterEqual(modeled_net + 1e-9, plan.risk_budget)

    def test_short_plan_is_symmetric_and_be_stop_covers_costs(self):
        plan = build_recovery_plan(
            equity=1_000,
            risk_fraction=0.01,
            entry=100,
            stop=101,
            obstacle=96,
            side="Sell",
            qty_step=0.001,
            min_qty=0.001,
            max_qty=1_000,
        )

        self.assertTrue(plan.accepted)
        self.assertGreater(plan.break_even_stop, plan.tp1)
        self.assertLess(plan.break_even_stop, 100)
        self.assertLess(plan.tp2, plan.tp1)

    def test_tight_fee_heavy_setup_is_rejected(self):
        plan = build_recovery_plan(
            equity=1_000,
            risk_fraction=0.01,
            entry=0.2575,
            stop=0.2579,
            obstacle=0.2504,
            side="Sell",
            qty_step=1,
            min_qty=1,
            max_qty=100_000,
        )

        self.assertFalse(plan.accepted)
        self.assertEqual(plan.reason_code, "FRICTION_TOO_HIGH")

    def test_recovery_target_must_fit_before_nearest_obstacle(self):
        plan = build_recovery_plan(
            equity=1_000,
            risk_fraction=0.01,
            entry=100,
            stop=99,
            obstacle=101.55,
            side="Buy",
            qty_step=0.001,
            min_qty=0.001,
            max_qty=1_000,
        )

        self.assertFalse(plan.accepted)
        self.assertEqual(plan.reason_code, "OBSTACLE_BEFORE_RECOVERY")

    def test_break_even_stop_includes_entry_and_exit_costs(self):
        long_stop = break_even_with_costs(100, "Buy", 0.00055, 0.00020)
        short_stop = break_even_with_costs(100, "Sell", 0.00055, 0.00020)

        self.assertGreater(long_stop, 100)
        self.assertLess(short_stop, 100)

    def test_shadow_mode_records_plan_without_order_side_effects(self):
        settings = Settings(
            api_key="demo-key",
            api_secret="demo-secret",
            enable_order_placement=False,
            v2_management_mode="shadow",
        )
        gateway = MagicMock()
        gateway.equity_usdt.return_value = 1_000
        gateway.instrument_rules.return_value = SimpleNamespace(
            qty_step=0.001, min_qty=0.001, max_market_qty=1_000
        )
        store = MagicMock()
        store.load_open_trades.return_value = {}
        engine = TradingEngine(settings, gateway, store, MagicMock())
        armed = ArmedSignal(
            pattern=PatternSignal(
                name="bullish_engulfing",
                side="Buy",
                timestamp_ms=1,
                trigger=100,
                stop=99,
                pattern_high=100,
                pattern_low=99,
                volume_ratio=1.5,
            ),
            armed_at_ms=1,
            expires_at_ms=2,
            target=104,
        )

        engine._record_v2_management_shadow("BTCUSDT", armed, 100)

        self.assertEqual(store.event.call_args.args[0], "V2_MANAGEMENT_SHADOW_ACCEPTED")
        gateway.place_market_order.assert_not_called()
        gateway.close_partial.assert_not_called()


if __name__ == "__main__":
    unittest.main()
