from __future__ import annotations

import logging
import signal
import threading
import time
import uuid

from .analysis import detect_pattern, market_context, reward_risk
from .config import Settings
from .exchange import BybitGateway
from .models import ArmedSignal, SignalState, Trade
from .notify import TelegramNotifier
from .risk import one_r_price, position_size
from .store import Store
from .universe import select_symbols


LOGGER = logging.getLogger(__name__)
FIVE_MINUTES_MS = 5 * 60 * 1000
UNIVERSE_REFRESH_MS = 24 * 60 * 60 * 1000


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        gateway: BybitGateway,
        store: Store,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.store = store
        self.symbols: list[str] = []
        self.last_universe_refresh_ms = 0
        self.last_processed_candle: dict[str, int] = {}
        self.armed: dict[str, ArmedSignal] = {}
        self.trades = store.load_open_trades()
        self.notifier = notifier or TelegramNotifier()
        self._stop_event = threading.Event()

    def refresh_universe(self, force: bool = False) -> None:
        now_ms = int(time.time() * 1000)
        if not force and now_ms - self.last_universe_refresh_ms < UNIVERSE_REFRESH_MS:
            return
        tickers = self.gateway.tickers()
        instruments = self.gateway.instruments()
        selected = select_symbols(tickers, instruments, self.settings.universe_size, now_ms)
        if len(selected) < self.settings.universe_size:
            raise RuntimeError(
                f"Only {len(selected)} symbols passed liquidity filters; expected "
                f"{self.settings.universe_size}"
            )
        # Never stop monitoring a symbol that still has an open local trade.
        self.symbols = list(dict.fromkeys(selected + list(self.trades)))
        self.last_universe_refresh_ms = now_ms
        self.store.event("UNIVERSE_UPDATED", symbols=self.symbols)
        LOGGER.info("Universe: %s", ", ".join(self.symbols))

    def run_once(self) -> None:
        self.refresh_universe(force=not self.symbols)
        ticker_rows = self.gateway.tickers()
        prices = {
            item["symbol"]: float(item["lastPrice"])
            for item in ticker_rows
            if item.get("lastPrice")
        }
        self._manage_open_trades(prices)
        entries_enabled = self.store.trading_enabled()
        for symbol in self.symbols:
            if symbol in self.trades:
                continue
            try:
                self._process_symbol(symbol, prices.get(symbol, 0.0), entries_enabled)
            except Exception:
                LOGGER.exception("Failed while processing %s", symbol)
                self.store.event("SYMBOL_ERROR", symbol)
            time.sleep(0.05)

    def _process_symbol(self, symbol: str, last_price: float, entries_enabled: bool = True) -> None:
        candles_5m = self.gateway.candles(symbol, "5", 120)
        if len(candles_5m) < 30:
            return
        latest = candles_5m[-1]
        if self.last_processed_candle.get(symbol) == latest.timestamp_ms:
            return
        self.last_processed_candle[symbol] = latest.timestamp_ms

        candles_1h = self.gateway.candles(symbol, "60", 200)
        context = market_context(candles_1h)
        LOGGER.info(
            "%s state=%s bias=%s close=%.8g",
            symbol,
            "ARMED" if symbol in self.armed else "SCAN",
            context.bias,
            latest.close,
        )
        support = context.support.center if context.support else None
        resistance = context.resistance.center if context.resistance else None
        visible_state = "PAUSED" if not entries_enabled else "ARMED" if symbol in self.armed else "SCAN"
        self.store.market_snapshot(
            symbol, last_price or latest.close, context.bias, support, resistance, visible_state
        )
        if not entries_enabled:
            self.armed.pop(symbol, None)
            return

        armed = self.armed.get(symbol)
        if armed:
            if latest.timestamp_ms > armed.expires_at_ms:
                self.store.event("SIGNAL_EXPIRED", symbol, pattern=armed.pattern.name)
                self.armed.pop(symbol, None)
                return
            invalid = (
                armed.pattern.side == "Buy" and latest.close <= armed.pattern.stop
            ) or (
                armed.pattern.side == "Sell" and latest.close >= armed.pattern.stop
            )
            if invalid:
                self.store.event("SIGNAL_INVALIDATED", symbol, pattern=armed.pattern.name)
                self.armed.pop(symbol, None)
                return
            is_later_candle = latest.timestamp_ms > armed.pattern.timestamp_ms
            triggered = (
                armed.pattern.side == "Buy" and latest.high >= armed.pattern.trigger
            ) or (
                armed.pattern.side == "Sell" and latest.low <= armed.pattern.trigger
            )
            if is_later_candle and triggered:
                entry = last_price or latest.close
                if reward_risk(entry, armed.pattern.stop, armed.target, armed.pattern.side) < self.settings.min_reward_risk:
                    self.store.event("SIGNAL_SKIPPED_RR", symbol, entry=entry, target=armed.target)
                else:
                    self._enter(symbol, armed, entry)
                self.armed.pop(symbol, None)
            return

        pattern = detect_pattern(candles_5m, context, self.settings.volume_multiplier)
        if pattern is None:
            return
        target = (
            context.resistance.center
            if pattern.side == "Buy" and context.resistance
            else context.support.center
            if pattern.side == "Sell" and context.support
            else 0.0
        )
        if target <= 0 or reward_risk(pattern.trigger, pattern.stop, target, pattern.side) < self.settings.min_reward_risk:
            self.store.event(
                "PATTERN_REJECTED_RR",
                symbol,
                pattern=pattern.name,
                trigger=pattern.trigger,
                stop=pattern.stop,
                target=target,
            )
            return
        armed = ArmedSignal(
            pattern=pattern,
            armed_at_ms=latest.timestamp_ms,
            expires_at_ms=latest.timestamp_ms + 4 * FIVE_MINUTES_MS,
            target=target,
        )
        self.armed[symbol] = armed
        self.store.event(
            "SIGNAL_ARMED",
            symbol,
            pattern=pattern.name,
            side=pattern.side,
            trigger=pattern.trigger,
            stop=pattern.stop,
            target=target,
            volume_ratio=pattern.volume_ratio,
        )
        self.notifier.send(
            f"ARMED {symbol} {pattern.side} {pattern.name}\n"
            f"trigger={pattern.trigger} stop={pattern.stop} target={target}\n"
            f"volume={pattern.volume_ratio:.2f}x"
        )
        LOGGER.info(
            "%s armed %s %s trigger=%s stop=%s target=%s volume=%.2fx",
            symbol,
            pattern.side,
            pattern.name,
            pattern.trigger,
            pattern.stop,
            target,
            pattern.volume_ratio,
        )

    def _enter(self, symbol: str, armed: ArmedSignal, expected_entry: float) -> None:
        if len(self.trades) >= self.settings.max_open_positions:
            self.store.event("ENTRY_BLOCKED_MAX_POSITIONS", symbol)
            return
        projected_risk = (len(self.trades) + 1) * self.settings.risk_per_trade
        if projected_risk > self.settings.max_total_open_risk + 1e-12:
            self.store.event("ENTRY_BLOCKED_TOTAL_RISK", symbol)
            return
        if not self.settings.enable_order_placement:
            self.store.event(
                "ENTRY_SIGNAL_ONLY",
                symbol,
                side=armed.pattern.side,
                expected_entry=expected_entry,
                stop=armed.pattern.stop,
                target=armed.target,
            )
            LOGGER.warning("Signal only: %s %s (order placement disabled)", armed.pattern.side, symbol)
            self.notifier.send(
                f"SIGNAL ONLY {symbol} {armed.pattern.side}\n"
                f"entry~{expected_entry} stop={armed.pattern.stop} target={armed.target}"
            )
            return

        equity = self.gateway.equity_usdt()
        rules = self.gateway.instrument_rules(symbol)
        margin_qty_cap = equity * self.settings.leverage * 0.90 / expected_entry
        qty = position_size(
            equity=equity,
            risk_fraction=self.settings.risk_per_trade,
            entry=expected_entry,
            stop=armed.pattern.stop,
            qty_step=rules.qty_step,
            min_qty=rules.min_qty,
            max_qty=min(rules.max_market_qty, margin_qty_cap),
        )
        if qty <= 0:
            self.store.event("ENTRY_BLOCKED_MIN_QTY", symbol)
            return

        self.gateway.set_leverage(symbol, self.settings.leverage)
        link_id = f"pa-{symbol[:8]}-{uuid.uuid4().hex[:12]}"
        order_id = self.gateway.place_market_order(symbol, armed.pattern.side, qty, link_id)
        try:
            position = self.gateway.wait_for_position(symbol)
            actual_entry = float(position["avgPrice"])
            actual_qty = float(position["size"])
            self.gateway.set_protection(symbol, armed.pattern.stop, armed.target)
        except Exception:
            LOGGER.exception("Protection failed after entry; emergency-closing %s", symbol)
            self.gateway.close_partial(symbol, armed.pattern.side, qty)
            self.store.event("EMERGENCY_CLOSE_UNPROTECTED", symbol, order_id=order_id)
            raise

        trade = Trade(
            symbol=symbol,
            side=armed.pattern.side,
            qty=actual_qty,
            entry=actual_entry,
            stop=armed.pattern.stop,
            target=armed.target,
            one_r_target=one_r_price(actual_entry, armed.pattern.stop, armed.pattern.side),
            order_id=order_id,
        )
        self.trades[symbol] = trade
        self.store.save_trade(trade)
        self.store.event("POSITION_OPENED", symbol, **{k: str(v) for k, v in trade.__dict__.items()})
        LOGGER.warning("Opened %s %s qty=%s entry=%s", trade.side, symbol, trade.qty, trade.entry)
        self.notifier.send(
            f"OPENED {symbol} {trade.side} qty={trade.qty}\n"
            f"entry={trade.entry} stop={trade.stop} target={trade.target}"
        )

    def _manage_open_trades(self, prices: dict[str, float]) -> None:
        if not self.settings.enable_order_placement:
            return
        for symbol, trade in list(self.trades.items()):
            try:
                position = self.gateway.position(symbol)
                if not position:
                    self.store.event("POSITION_CLOSED", symbol)
                    self.store.close_trade(symbol)
                    self.trades.pop(symbol, None)
                    self.notifier.send(f"CLOSED {symbol}")
                    continue
                price = prices.get(symbol) or float(position["markPrice"])
                reached_one_r = (
                    trade.side == "Buy" and price >= trade.one_r_target
                ) or (
                    trade.side == "Sell" and price <= trade.one_r_target
                )
                if reached_one_r and not trade.partial_taken:
                    self.gateway.close_partial(symbol, trade.side, trade.qty / 2)
                    fee_buffer = trade.entry * 0.0007
                    new_stop = (
                        trade.entry + fee_buffer
                        if trade.side == "Buy"
                        else trade.entry - fee_buffer
                    )
                    self.gateway.move_stop(symbol, new_stop)
                    trade.stop = new_stop
                    trade.partial_taken = True
                    trade.state = SignalState.PARTIAL_TP
                    self.store.save_trade(trade)
                    self.store.event("PARTIAL_TP", symbol, price=price, new_stop=new_stop)
                    self.notifier.send(
                        f"PARTIAL TP {symbol} at {price}\nremaining stop={new_stop}"
                    )

                risk = abs(trade.entry - trade.one_r_target)
                reached_trailing = (
                    trade.side == "Buy" and price >= trade.entry + 1.5 * risk
                ) or (
                    trade.side == "Sell" and price <= trade.entry - 1.5 * risk
                )
                if trade.partial_taken and reached_trailing:
                    candles = self.gateway.candles(symbol, "5", 8)
                    if len(candles) >= 4:
                        recent = candles[-4:-1]
                        candidate = (
                            min(c.low for c in recent)
                            if trade.side == "Buy"
                            else max(c.high for c in recent)
                        )
                        improves = (
                            trade.side == "Buy" and candidate > trade.stop
                        ) or (
                            trade.side == "Sell" and candidate < trade.stop
                        )
                        if improves:
                            self.gateway.move_stop(symbol, candidate)
                            trade.stop = candidate
                            trade.state = SignalState.TRAILING
                            self.store.save_trade(trade)
                            self.store.event("TRAILING_STOP_MOVED", symbol, stop=candidate)
            except Exception:
                LOGGER.exception("Trade management failed for %s", symbol)
                self.store.event("TRADE_MANAGEMENT_ERROR", symbol)

    def run_forever(self) -> None:
        def request_stop(signum: int, _frame: object) -> None:
            LOGGER.warning("Received signal %s; stopping after current cycle", signum)
            self._stop_event.set()

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, request_stop)
            signal.signal(signal.SIGINT, request_stop)
        self.refresh_universe(force=True)
        mode = "DEMO ORDERS" if self.settings.enable_order_placement else "SIGNAL ONLY"
        LOGGER.warning("Bot started in %s mode", mode)
        self.store.heartbeat(
            "running", mode=mode, symbols=self.symbols, entries_enabled=self.store.trading_enabled()
        )
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.run_once()
                self.store.heartbeat(
                    "running",
                    mode=mode,
                    symbols=self.symbols,
                    entries_enabled=self.store.trading_enabled(),
                    open_positions=len(self.trades),
                )
            except Exception:
                LOGGER.exception("Engine cycle failed")
                self.store.event("ENGINE_CYCLE_ERROR")
                self.store.heartbeat("error", mode=mode)
            elapsed = time.monotonic() - started
            self._stop_event.wait(max(1.0, self.settings.poll_seconds - elapsed))
        self.store.heartbeat("stopped", mode=mode)
        LOGGER.warning("Bot stopped cleanly")
