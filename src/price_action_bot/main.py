from __future__ import annotations

import argparse
import logging
from datetime import datetime

from .config import Settings
from .engine import TradingEngine
from .exchange import BybitGateway
from .notify import TelegramNotifier
from .store import Store
from .v2_foundation import replay_v1_checklist


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bybit price-action trading bot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan cycle and exit (useful for setup verification)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show local open trades and recent events without contacting Bybit",
    )
    parser.add_argument(
        "--replay-v1",
        nargs="?",
        const=100,
        default=0,
        type=int,
        metavar="LIMIT",
        help="Replay recent Phase-0 V1 decision records without contacting Bybit",
    )
    return parser


def _print_status(store: Store) -> None:
    trades = store.load_open_trades()
    print(f"Open local trades: {len(trades)}")
    for trade in trades.values():
        print(
            f"  {trade.symbol} {trade.side} qty={trade.qty} entry={trade.entry} "
            f"stop={trade.stop} target={trade.target} state={trade.state.value}"
        )
    print("Recent events:")
    for row in store.recent_events(15):
        timestamp = datetime.fromtimestamp(row["created_at_ms"] / 1000).astimezone().isoformat(
            timespec="seconds"
        )
        print(f"  {timestamp} {row['event_type']} {row['symbol']} {row['payload']}")


def _replay_v1(store: Store, limit: int) -> bool:
    records = store.thesisedge_decisions(max(1, limit))
    if not records:
        print("No ThesisEdge Phase-0 decision records are available yet.")
        return True
    passed = 0
    for record in records:
        references = record["candle_references"]
        candles_5m = store.thesisedge_candles(
            record["symbol"], "5m", references["5m"]["from_ms"], references["5m"]["to_ms"]
        )
        candles_1h = store.thesisedge_candles(
            record["symbol"], "1h", references["1h"]["from_ms"], references["1h"]["to_ms"]
        )
        result = replay_v1_checklist(record, candles_5m, candles_1h)
        matches = bool(result["inputs_match"] and result["decision_matches"])
        passed += int(matches)
        print(
            f"{'PASS' if matches else 'FAIL'} {record['symbol']} "
            f"candle={record['candle_time_ms']} id={record['decision_id'][:12]}"
        )
    print(f"Replay summary: {passed}/{len(records)} deterministic decisions matched")
    return passed == len(records)


def main() -> None:
    args = _parser().parse_args()
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    store = Store(settings.database_path, settings.database_url)
    if args.replay_v1:
        if not _replay_v1(store, args.replay_v1):
            raise SystemExit(1)
        return
    if args.status:
        _print_status(store)
        return
    gateway = BybitGateway(settings.api_key, settings.api_secret, settings.demo)
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    engine = TradingEngine(settings, gateway, store, notifier)
    if args.once:
        engine.run_once()
    else:
        engine.run_forever()


if __name__ == "__main__":
    main()
