from __future__ import annotations

import argparse
import logging
from datetime import datetime

from .config import Settings
from .engine import TradingEngine
from .exchange import BybitGateway
from .notify import TelegramNotifier
from .store import Store


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


def main() -> None:
    args = _parser().parse_args()
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    store = Store(settings.database_path, settings.database_url)
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
