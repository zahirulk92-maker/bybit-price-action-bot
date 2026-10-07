import tempfile
import unittest
from pathlib import Path

from price_action_bot.models import Candle
from price_action_bot.store import Store
from price_action_bot.structure import (
    STRUCTURE_SCHEMA_VERSION,
    StructureParameters,
    build_structure_map,
    confirmed_history_is_stable,
    confirmed_swings,
)


def candles_from(values: list[float], step_ms: int = 3_600_000) -> list[Candle]:
    return [
        Candle(index * step_ms, value - 0.2, value + 0.5, value - 0.5, value, 100 + index)
        for index, value in enumerate(values)
    ]


class StructureMapTests(unittest.TestCase):
    def setUp(self):
        self.params = StructureParameters(
            internal_swing_width=1,
            major_swing_width=2,
            atr_period=3,
            zone_atr_multiplier=0.15,
            zone_min_price_fraction=0.0005,
            weakened_after_touches=2,
            break_buffer_atr=0.0,
            invalidation_buffer_atr=0.5,
            failed_break_window=3,
            max_debug_zones=20,
        )

    def test_swing_is_confirmed_only_after_closed_right_bars(self):
        values = [10, 11, 15, 12]
        too_early = candles_from(values)
        self.assertEqual(confirmed_swings(too_early, 2, "major"), [])

        confirmed = confirmed_swings(candles_from(values + [11]), 2, "major")
        high = next(item for item in confirmed if item.kind == "high")
        self.assertEqual(high.source_index, 2)
        self.assertEqual(high.confirmation_index, 4)
        self.assertEqual(high.confirmed_at_ms, 4 * 3_600_000)

    def test_confirmed_history_never_moves_when_future_candles_arrive(self):
        values = [10, 12, 15, 13, 9, 11, 16, 14, 10, 12, 17, 15, 11, 13, 18, 16, 12]
        earlier = build_structure_map(candles_from(values[:13]), self.params)
        later = build_structure_map(candles_from(values), self.params)
        self.assertTrue(earlier["major_swings"])
        self.assertTrue(confirmed_history_is_stable(earlier, later))

    def test_map_contains_auditable_zones_breaks_and_liquidity_events(self):
        values = [
            10, 12, 15, 12, 9, 11, 14, 11, 10, 12,
            14.4, 13, 10.5, 12, 15.8, 13, 9.2, 11, 16.5, 14,
        ]
        snapshot = build_structure_map(candles_from(values), self.params)
        self.assertEqual(snapshot["schema_version"], STRUCTURE_SCHEMA_VERSION)
        self.assertEqual(snapshot["source"]["closed_candle_count"], len(values))
        self.assertTrue(snapshot["source"]["sha256"])
        self.assertTrue(snapshot["major_swings"])
        self.assertTrue(snapshot["zones"])
        self.assertTrue(snapshot["events"])
        self.assertTrue({event["kind"] for event in snapshot["events"]} & {"BOS", "CHOCH", "SWEEP", "FAILED_BREAK"})
        for swing in snapshot["major_swings"]:
            self.assertGreaterEqual(swing["confirmed_at_ms"], swing["source_time_ms"])
            self.assertIn(swing["label"], {"H?", "L?", "HH", "LH", "HL", "LL", "EH", "EL"})
        for zone in snapshot["zones"]:
            self.assertIn(zone["state"], {"fresh", "tested", "weakened", "broken", "flip-watch", "invalid"})
            self.assertIn("touch_count", zone)
            self.assertIn("age_bars", zone)
            self.assertIn("invalidated_at_ms", zone)

    def test_snapshot_is_deterministic_and_store_deduplicates_it(self):
        candles = candles_from([10, 12, 15, 12, 9, 11, 16, 13, 10, 12, 17])
        first = build_structure_map(candles, self.params)
        second = build_structure_map(candles, self.params)
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            store = Store(str(Path(directory) / "phase1.db"))
            try:
                store.record_thesisedge_structure("BTCUSDT", first)
                store.record_thesisedge_structure("BTCUSDT", second)
                loaded = store.latest_thesisedge_structure("BTCUSDT")
                self.assertEqual(loaded["snapshot_id"], first["snapshot_id"])
                row = store._execute(
                    "SELECT COUNT(*) AS total FROM thesisedge_structure_snapshots",
                    fetch="one",
                )
                self.assertEqual(int(store._dict(row)["total"]), 1)
            finally:
                store.connection.close()


if __name__ == "__main__":
    unittest.main()
