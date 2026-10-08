import unittest
from unittest.mock import MagicMock

from price_action_bot.config import Settings
from price_action_bot.engine import TradingEngine
from price_action_bot.models import Candle
from price_action_bot.playbooks import PlaybookParameters, build_playbook_context


def candles(count: int, interval_ms: int, close: float = 100.0, start_ms: int = 0) -> list[Candle]:
    return [
        Candle(start_ms + (index + 1) * interval_ms, close, close + 1, close - 1, close, 1000 + index)
        for index in range(count)
    ]


def structure(state: str, kind: str, zone_state: str = "fresh", break_direction: str = ""):
    zone = {
        "zone_id": "zone-1", "kind": kind, "state": zone_state,
        "center": 100.0, "lower": 99.5, "upper": 100.5, "width": 1.0,
    }
    events = []
    if break_direction:
        events.append({
            "kind": "BOS", "direction": break_direction,
            "confirmed_at_ms": 59 * 3_600_000, "level": 100.0,
        })
    return {
        "state": state, "data_quality": "complete", "zones": [zone], "events": events,
    }


class PlaybookTests(unittest.TestCase):
    def setUp(self):
        self.params = PlaybookParameters(min_1h_candles=60, min_5m_candles=30)
        self.hourly = candles(60, 3_600_000)
        self.five = candles(30, 5 * 60_000, start_ms=57 * 3_600_000 + 30 * 60_000)

    def match(self, market_structure):
        return build_playbook_context(
            "TESTUSDT", self.hourly, self.five, market_structure, self.params
        )

    def test_trend_pullback_is_symmetric(self):
        bullish = self.match(structure("HH_HL", "support"))
        bearish = self.match(structure("LH_LL", "resistance"))
        self.assertEqual(bullish["status"], "MATCHED")
        self.assertEqual(bullish["selected_playbook"]["playbook"], "TREND_PULLBACK")
        self.assertEqual(bullish["selected_playbook"]["direction"], "Buy")
        self.assertEqual(bearish["selected_playbook"]["playbook"], "TREND_PULLBACK")
        self.assertEqual(bearish["selected_playbook"]["direction"], "Sell")
        self.assertEqual(bullish["selected_playbook"]["invalidation"]["side"], "below")
        self.assertEqual(bearish["selected_playbook"]["invalidation"]["side"], "above")

    def test_range_reversal_is_symmetric(self):
        support = self.match(structure("range", "support"))
        resistance = self.match(structure("range", "resistance"))
        self.assertEqual(support["selected_playbook"]["playbook"], "RANGE_REVERSAL")
        self.assertEqual(support["selected_playbook"]["direction"], "Buy")
        self.assertEqual(resistance["selected_playbook"]["playbook"], "RANGE_REVERSAL")
        self.assertEqual(resistance["selected_playbook"]["direction"], "Sell")

    def test_breakout_retest_is_symmetric(self):
        bullish = self.match(structure("HH_HL", "resistance", "flip-watch", "bullish"))
        bearish = self.match(structure("LH_LL", "support", "flip-watch", "bearish"))
        self.assertEqual(bullish["selected_playbook"]["playbook"], "BREAKOUT_RETEST")
        self.assertEqual(bullish["selected_playbook"]["direction"], "Buy")
        self.assertEqual(bullish["selected_playbook"]["invalidation"]["side"], "below")
        self.assertEqual(bullish["selected_playbook"]["invalidation"]["price"], 99.5)
        self.assertEqual(bearish["selected_playbook"]["playbook"], "BREAKOUT_RETEST")
        self.assertEqual(bearish["selected_playbook"]["direction"], "Sell")
        self.assertEqual(bearish["selected_playbook"]["invalidation"]["side"], "above")
        self.assertEqual(bearish["selected_playbook"]["invalidation"]["price"], 100.5)

    def test_mid_range_cannot_masquerade_as_setup(self):
        market_structure = structure("range", "support")
        market_structure["zones"][0].update(center=80.0, lower=79.5, upper=80.5)
        result = self.match(market_structure)
        self.assertEqual(result["status"], "NO_MATCHING_PLAYBOOK")
        self.assertEqual(result["reason_code"], "MID_RANGE_NO_EDGE")
        self.assertIsNone(result["selected_playbook"])

    def test_insufficient_data_is_unknown(self):
        result = build_playbook_context(
            "TESTUSDT", self.hourly[:20], self.five[:10], structure("range", "support"), self.params
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["context"]["data_quality"], "insufficient")
        self.assertIsNone(result["selected_playbook"])
        self.assertFalse(result["v2_execution_authority"])
        self.assertFalse(result["risk_authority"])

    def test_snapshot_is_deterministic(self):
        first = self.match(structure("range", "support"))
        second = self.match(structure("range", "support"))
        self.assertEqual(first, second)

    def test_engine_records_shadow_evidence_without_order_or_risk_authority(self):
        store = MagicMock()
        store.load_open_trades.return_value = {}
        store.expire_armed_signal_journal.return_value = 0
        gateway = MagicMock()
        engine = TradingEngine(
            Settings("demo", "secret", v2_playbook_mode="shadow"), gateway, store, MagicMock()
        )

        engine._record_phase4_playbook(
            "TESTUSDT", self.hourly, self.five, structure("range", "support")
        )

        snapshot = store.record_thesisedge_playbook.call_args.args[0]
        self.assertEqual(snapshot["status"], "MATCHED")
        self.assertFalse(snapshot["v2_execution_authority"])
        self.assertFalse(snapshot["risk_authority"])
        self.assertFalse(gateway.place_market_order.called)


if __name__ == "__main__":
    unittest.main()
