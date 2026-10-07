import math
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from price_action_bot.analysis import market_context, setup_checklist
from price_action_bot.config import Settings
from price_action_bot.main import _replay_v1
from price_action_bot.models import Candle
from price_action_bot.store import Store
from price_action_bot.v2_foundation import (
    DECISION_SCHEMA_VERSION,
    V1_POLICY_VERSION,
    V2FeatureFlags,
    build_v1_decision_audit,
    replay_v1_checklist,
)


def sample_candles(interval_ms: int, count: int, base: float = 100.0) -> list[Candle]:
    rows = []
    for index in range(count):
        center = base + math.sin(index / 2) * 3 + index * 0.03
        rows.append(
            Candle(
                timestamp_ms=(index + 1) * interval_ms,
                open=center - 0.2,
                high=center + 1,
                low=center - 1,
                close=center + 0.2,
                volume=100 + index,
            )
        )
    return rows


class ThesisEdgeFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.store = Store(str(Path(self.temp_dir.name) / "phase0.db"))

    def tearDown(self):
        self.store.connection.close()
        self.temp_dir.cleanup()

    def test_phase0_flags_have_no_execution_authority(self):
        settings = Settings("", "")
        self.assertTrue(settings.v2_instrumentation_enabled)
        self.assertEqual(settings.v2_feature_modes()["structure"], "shadow")
        self.assertEqual(
            {mode for name, mode in settings.v2_feature_modes().items() if name != "structure"},
            {"off"},
        )
        flags = V2FeatureFlags(
            structure="shadow",
            universe="shadow",
            portfolio="shadow",
            playbooks="shadow",
            thesis="shadow",
            management="shadow",
        )
        self.assertFalse(flags.has_execution_authority)
        self.assertEqual(flags.as_dict()["thesis"], "shadow")
        with self.assertRaisesRegex(ValueError, "must be off or shadow"):
            V2FeatureFlags(structure="active")
        with self.assertRaisesRegex(ValueError, "must be off or shadow"):
            Settings("", "", v2_structure_mode="active").validate()
        with self.assertRaisesRegex(ValueError, "requires V2_UNIVERSE_MODE=shadow"):
            Settings("", "", v2_portfolio_mode="shadow").validate()

    def test_decision_record_is_deduplicated_and_replays(self):
        candles_5m = sample_candles(300_000, 30)
        candles_1h = sample_candles(3_600_000, 60)
        context = market_context(candles_1h)
        decision = setup_checklist(candles_5m, context, 1.2, 1.5)
        record = build_v1_decision_audit(
            symbol="BTCUSDT",
            candles_5m=candles_5m,
            candles_1h=candles_1h,
            context=context,
            decision=decision,
            policy_settings={
                "volume_multiplier": 1.2,
                "min_reward_risk": 1.5,
                "risk_per_trade": 0.01,
            },
            runtime_state={"entries_enabled": True, "armed_before_evaluation": False},
            feature_flags=V2FeatureFlags(),
        )
        self.assertEqual(record["policy_version"], V1_POLICY_VERSION)
        self.assertEqual(record["schema_version"], DECISION_SCHEMA_VERSION)
        self.assertFalse(record["input_payload"]["v2_execution_authority"])

        self.store.record_thesisedge_decision(record, candles_5m, candles_1h)
        self.store.record_thesisedge_decision(record, candles_5m, candles_1h)
        saved = self.store.thesisedge_decisions()
        self.assertEqual(len(saved), 1)

        references = saved[0]["candle_references"]
        archived_5m = self.store.thesisedge_candles(
            "BTCUSDT", "5m", references["5m"]["from_ms"], references["5m"]["to_ms"]
        )
        archived_1h = self.store.thesisedge_candles(
            "BTCUSDT", "1h", references["1h"]["from_ms"], references["1h"]["to_ms"]
        )
        result = replay_v1_checklist(saved[0], archived_5m, archived_1h)
        self.assertTrue(result["inputs_match"])
        self.assertTrue(result["decision_matches"])
        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(_replay_v1(self.store, 10))
        self.assertIn("Replay summary: 1/1", output.getvalue())

    def test_replay_detects_tampered_market_input(self):
        candles_5m = sample_candles(300_000, 30)
        candles_1h = sample_candles(3_600_000, 60)
        context = market_context(candles_1h)
        decision = setup_checklist(candles_5m, context, 1.2, 1.5)
        record = build_v1_decision_audit(
            symbol="ETHUSDT",
            candles_5m=candles_5m,
            candles_1h=candles_1h,
            context=context,
            decision=decision,
            policy_settings={"volume_multiplier": 1.2, "min_reward_risk": 1.5},
            runtime_state={},
            feature_flags=V2FeatureFlags(),
        )
        changed = list(candles_5m)
        last = changed[-1]
        changed[-1] = Candle(
            last.timestamp_ms,
            last.open,
            last.high,
            last.low,
            last.close + 1,
            last.volume,
        )
        result = replay_v1_checklist(record, changed, candles_1h)
        self.assertFalse(result["inputs_match"])


if __name__ == "__main__":
    unittest.main()
