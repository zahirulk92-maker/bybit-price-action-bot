from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, timedelta, timezone

from .analysis import detect_pattern, detect_trade_reversal, market_context, reward_risk, setup_checklist
from .config import Settings
from .exchange import BybitGateway
from .management import build_recovery_plan
from .models import ArmedSignal, Candle, MarketContext, SignalState, Trade
from .notify import TelegramNotifier, format_alert
from .risk import one_r_price, position_size
from .store import Store
from .structure import build_structure_map
from .universe import select_symbols
from .v2_foundation import V2FeatureFlags, build_v1_decision_audit


LOGGER = logging.getLogger(__name__)
FIVE_MINUTES_MS = 5 * 60 * 1000
UNIVERSE_REFRESH_MS = 24 * 60 * 60 * 1000
RECONCILE_INTERVAL_MS = 60 * 1000


def _side_label(side: str) -> str:
    return "🟢 LONG / BUY" if side == "Buy" else "🔴 SHORT / SELL"


def _signal_message(symbol: str, side: str, pattern: str, trigger: float, stop: float, target: float, volume_ratio: float) -> str:
    rr = reward_risk(trigger, stop, target, side) if target > 0 else 0.0
    return (
        "📡 SIGNAL ARMED\n"
        "━━━━━━━━━━━━━━━━\n"
        f"📌 Pair: {symbol}\n"
        f"📊 Bias: {_side_label(side)}\n"
        f"🕯 Pattern: {pattern.replace('_', ' ')}\n"
        f"🎯 Trigger: {trigger:.8g}\n"
        f"🛡 Stop loss: {stop:.8g}\n"
        f"🏁 Zone target: {target:.8g}\n"
        f"⚖️ R:R: 1:{rr:.2f}\n"
        f"📈 Volume: {volume_ratio:.2f}× average\n"
        "⏳ Status: Waiting for trigger + confirmation\n"
        "⚠️ No entry before confirmation"
    )


def _set_check(
    decision: dict[str, object], key: str, status: str, detail: str
) -> None:
    for check in decision.get("checks", []):
        if isinstance(check, dict) and check.get("key") == key:
            check["status"] = status
            check["detail"] = detail
            return


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
        self.v2_feature_flags = V2FeatureFlags(**settings.v2_feature_modes())
        self.symbols: list[str] = []
        self.last_universe_refresh_ms = 0
        self.last_processed_candle: dict[str, int] = {}
        self.armed: dict[str, ArmedSignal] = {}
        self.journal_ids: dict[str, int] = {}
        self.trades = store.load_open_trades()
        expired_signals = store.expire_armed_signal_journal(
            "Worker restarted before the trigger; setup must be armed again"
        )
        if isinstance(expired_signals, int) and expired_signals:
            store.event("STALE_SIGNALS_EXPIRED", count=expired_signals)
        self.notifier = notifier or TelegramNotifier()
        self._stop_event = threading.Event()
        self.last_daily_report_key = ""
        self.last_reconcile_ms = 0
        self.last_pnl_poll_ms = 0
        self.last_daily_loss_check_ms = 0
        self.daily_loss_alert_key = ""
        self.daily_loss: dict[str, object] = {
            "available": not settings.enable_order_placement,
            "breached": False,
            "limit_fraction": settings.daily_max_net_loss,
            "realized_pnl": 0.0,
            "starting_capital": 0.0,
            "max_loss_usdt": 0.0,
            "remaining_usdt": 0.0,
        }
        self.notified_closed_pnl_ids: set[str] = set()
        self.blocked_symbols: set[str] = set()
        self.reconciliation: dict[str, object] = {
            "status": "pending",
            "last_checked_at_ms": 0,
            "exchange_open": 0,
            "tracked_open": len(self.trades),
            "blocked_symbols": [],
            "issues": [],
        }

    def _record_phase0_baseline(
        self,
        symbol: str,
        candles_5m: list[Candle],
        candles_1h: list[Candle],
        context: MarketContext,
        decision: dict[str, object],
        last_price: float,
        entries_enabled: bool,
    ) -> None:
        """Record replay inputs without changing or authorizing the V1 decision."""
        if not self.settings.v2_instrumentation_enabled:
            return
        try:
            record = build_v1_decision_audit(
                symbol=symbol,
                candles_5m=candles_5m,
                candles_1h=candles_1h,
                context=context,
                decision=decision,
                policy_settings={
                    "volume_multiplier": self.settings.volume_multiplier,
                    "min_reward_risk": self.settings.min_reward_risk,
                    "risk_per_trade": self.settings.risk_per_trade,
                    "max_open_positions": self.settings.max_open_positions,
                    "max_total_open_risk": self.settings.max_total_open_risk,
                    "daily_max_net_loss": self.settings.daily_max_net_loss,
                    "leverage": self.settings.leverage,
                },
                runtime_state={
                    "entries_enabled": entries_enabled,
                    "armed_before_evaluation": symbol in self.armed,
                    "open_trade": symbol in self.trades,
                    "last_price": last_price,
                },
                feature_flags=self.v2_feature_flags,
            )
            self.store.record_thesisedge_decision(record, candles_5m, candles_1h)
        except Exception:
            # Phase-0 observation must never alter V1 entry, management, or execution.
            LOGGER.exception("ThesisEdge Phase-0 audit failed for %s", symbol)
            try:
                self.store.event("THESISEDGE_AUDIT_ERROR", symbol)
            except Exception:
                LOGGER.exception("Could not persist ThesisEdge audit failure for %s", symbol)

    def _record_phase1_structure(self, symbol: str, candles_1h: list[Candle]) -> None:
        """Run the Phase-1 structure engine in observation-only shadow mode."""
        if self.settings.v2_structure_mode != "shadow":
            return
        try:
            snapshot = build_structure_map(candles_1h, self.settings.structure_parameters())
            self.store.archive_thesisedge_candles(symbol, "1h", candles_1h)
            self.store.record_thesisedge_structure(symbol, snapshot, mode="shadow")
        except Exception:
            # Structure output has no authority over V1 decisions or orders.
            LOGGER.exception("ThesisEdge Phase-1 structure audit failed for %s", symbol)
            try:
                self.store.event("THESISEDGE_STRUCTURE_ERROR", symbol)
            except Exception:
                LOGGER.exception("Could not persist structure failure for %s", symbol)

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
        self._reconcile_exchange_positions()
        self._notify_new_closed_pnl()
        self._maybe_send_daily_report()
        daily_loss = self._check_daily_loss_limit()
        entries_enabled = self.store.trading_enabled() and bool(daily_loss.get("available")) and not bool(daily_loss.get("breached"))
        for symbol in self.symbols:
            if symbol in self.trades or symbol in self.blocked_symbols:
                continue
            try:
                self._process_symbol(symbol, prices.get(symbol, 0.0), entries_enabled)
            except Exception:
                LOGGER.exception("Failed while processing %s", symbol)
                self.store.event("SYMBOL_ERROR", symbol)
            time.sleep(0.05)

    def _maybe_send_daily_report(self) -> None:
        """Send one compact daily operational report at the configured Dhaka time."""
        dhaka = timezone(timedelta(hours=6))
        now = datetime.now(dhaka)
        if (now.hour, now.minute) < (self.settings.daily_report_hour, self.settings.daily_report_minute):
            return
        report_key = now.date().isoformat()
        if report_key == self.last_daily_report_key:
            return
        start = datetime.combine(now.date(), datetime.min.time(), tzinfo=dhaka)
        events = self.store.events_since(int(start.timestamp() * 1000), limit=1000)
        counts: dict[str, int] = {}
        for event in events:
            counts[event["event_type"]] = counts.get(event["event_type"], 0) + 1
        opened = counts.get("POSITION_OPENED", 0)
        closed = sum(
            counts.get(name, 0)
            for name in (
                "POSITION_CLOSED", "TP3_CLOSED", "STOP_LOSS_CLOSED",
                "TRAILING_STOP_CLOSED", "EARLY_EXIT_REVERSAL",
            )
        )
        pnl_line = "💵 Realized P&L: unavailable"
        if self.settings.enable_order_placement:
            try:
                pnl_rows = self.gateway.closed_pnl(int(start.timestamp() * 1000), limit=200)
                realized = sum(float(row.get("closedPnl") or 0) for row in pnl_rows)
                fees = sum(
                    abs(float(row.get("openFee") or 0))
                    + abs(float(row.get("closeFee") or 0))
                    for row in pnl_rows
                )
                wins = sum(float(row.get("closedPnl") or 0) > 0 for row in pnl_rows)
                losses = sum(float(row.get("closedPnl") or 0) < 0 for row in pnl_rows)
                pnl_line = (
                    f"💵 Bybit realized P&L: {realized:+.4f} USDT\n"
                    f"🧾 Fees: {fees:.4f} USDT · Result: {wins}W / {losses}L"
                )
            except Exception:
                LOGGER.exception("Daily real P&L fetch failed")
        report = (
            "📊 DAILY TRADING REPORT\n"
            "━━━━━━━━━━━━━━━━\n"
            f"📅 Date: {now.strftime('%d %b %Y')} · Asia/Dhaka\n"
            f"🔎 Signals armed: {counts.get('SIGNAL_ARMED', 0)}\n"
            f"✅ Positions opened: {opened}\n"
            f"🏁 Positions closed: {closed}\n"
            f"💰 TP1 / TP2 / TP3: {counts.get('PARTIAL_TP', 0)} / "
            f"{counts.get('PARTIAL_TP2', 0)} / {counts.get('TP3_CLOSED', 0)}\n"
            f"⚠️ Reversal exits: {counts.get('EARLY_EXIT_REVERSAL', 0)}\n"
            f"🛡 SL / trailing exits: {counts.get('STOP_LOSS_CLOSED', 0)} / {counts.get('TRAILING_STOP_CLOSED', 0)}\n"
            f"{pnl_line}\n"
            f"🛑 Risk/emergency events: {counts.get('EMERGENCY_CLOSE_UNPROTECTED', 0) + counts.get('TRADE_MANAGEMENT_ERROR', 0)}\n"
            f"📌 Open now: {len(self.trades)}\n"
            f"🟢 Bot: {'ACTIVE' if self.settings.enable_order_placement else 'SIGNAL ONLY / DEMO'}"
        )
        self.notifier.send(report)
        self.store.event("DAILY_REPORT_SENT", report_date=report_key, **counts)
        self.last_daily_report_key = report_key

    def _check_daily_loss_limit(self, force: bool = False) -> dict[str, object]:
        """Fail closed on new entries when today's Bybit net P&L reaches the cap."""
        if not self.settings.enable_order_placement:
            self.daily_loss.update(available=True, breached=False)
            return self.daily_loss
        now_ms = int(time.time() * 1000)
        if not force and now_ms - self.last_daily_loss_check_ms < 15_000:
            return self.daily_loss
        dhaka = timezone(timedelta(hours=6))
        local_now = datetime.now(dhaka)
        start = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=dhaka)
        report_key = local_now.date().isoformat()
        try:
            rows = self.gateway.closed_pnl(int(start.timestamp() * 1000), limit=500)
            realized = sum(float(row.get("closedPnl") or 0) for row in rows)
            wallet = self.gateway.wallet_summary()
            wallet_balance = float(wallet.get("wallet_balance") or 0)
            starting_capital = max(0.0, wallet_balance - realized)
            max_loss = starting_capital * self.settings.daily_max_net_loss
            breached = starting_capital > 0 and realized <= -max_loss
            self.daily_loss = {
                "available": True,
                "breached": breached,
                "limit_fraction": self.settings.daily_max_net_loss,
                "realized_pnl": realized,
                "starting_capital": starting_capital,
                "max_loss_usdt": max_loss,
                "remaining_usdt": max(0.0, max_loss + realized),
                "checked_at_ms": now_ms,
                "reset_at": f"{(local_now + timedelta(days=1)).date().isoformat()} 00:00 Asia/Dhaka",
            }
            self.last_daily_loss_check_ms = now_ms
            if breached and self.daily_loss_alert_key != report_key:
                self.daily_loss_alert_key = report_key
                self.armed.clear()
                self.store.event(
                    "DAILY_LOSS_LIMIT_REACHED",
                    realized_pnl=realized,
                    starting_capital=starting_capital,
                    max_loss_usdt=max_loss,
                    limit_fraction=self.settings.daily_max_net_loss,
                )
                self.notifier.send(format_alert(
                    "🛑 DAILY LOSS LIMIT REACHED",
                    status="NEW ENTRIES LOCKED",
                    facts=[
                        ("💵 Bybit net P&L", f"{realized:+.4f} USDT"),
                        ("🏦 Start capital", f"{starting_capital:.4f} USDT"),
                        ("⛔ Daily limit", f"{self.settings.daily_max_net_loss * 100:.2f}% · {max_loss:.4f} USDT"),
                    ],
                    action="Open positions remain protected; entry lock resets at 00:00 Asia/Dhaka",
                ))
            if not breached and self.daily_loss_alert_key != report_key:
                self.daily_loss_alert_key = ""
        except Exception as exc:
            LOGGER.exception("Daily loss guard could not verify Bybit P&L")
            self.daily_loss = {
                "available": False,
                "breached": False,
                "limit_fraction": self.settings.daily_max_net_loss,
                "error": str(exc),
                "checked_at_ms": now_ms,
            }
            self.last_daily_loss_check_ms = now_ms
        return self.daily_loss

    def _process_symbol(self, symbol: str, last_price: float, entries_enabled: bool = True) -> None:
        candles_5m = self.gateway.candles(symbol, "5", 120)
        if len(candles_5m) < 30:
            return
        latest = candles_5m[-1]
        if self.last_processed_candle.get(symbol) == latest.timestamp_ms:
            return
        self.last_processed_candle[symbol] = latest.timestamp_ms

        candles_1h = self.gateway.candles(symbol, "60", 200)
        self._record_phase1_structure(symbol, candles_1h)
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

        def save_market(state: str) -> None:
            self.store.market_snapshot(
                symbol, last_price or latest.close, context.bias, support, resistance, state
            )

        visible_state = "PAUSED" if not entries_enabled else "ARMED" if symbol in self.armed else "SCAN"
        save_market(visible_state)
        decision = setup_checklist(
            candles_5m,
            context,
            self.settings.volume_multiplier,
            self.settings.min_reward_risk,
        )
        self._record_phase0_baseline(
            symbol,
            candles_5m,
            candles_1h,
            context,
            decision,
            last_price or latest.close,
            entries_enabled,
        )
        if not entries_enabled:
            self.armed.pop(symbol, None)
            self.store.update_signal_journal(
                self.journal_ids.pop(symbol, None), symbol, "PAUSED", reason="Entries paused before trigger"
            )
            decision["summary"] = "Entries are paused — analysis continues without arming setups"
            _set_check(decision, "trigger", "wait", "Resume entries to allow a setup to arm")
            self.store.decision_snapshot(symbol, decision)
            return

        armed = self.armed.get(symbol)
        if armed:
            _set_check(
                decision,
                "pattern",
                "pass",
                armed.pattern.name.replace("_", " ").title(),
            )
            _set_check(
                decision,
                "volume",
                "pass",
                f"{armed.pattern.volume_ratio:.2f}× passed the volume gate",
            )
            armed_rr = reward_risk(
                armed.pattern.trigger, armed.pattern.stop, armed.target, armed.pattern.side
            )
            _set_check(
                decision,
                "rr",
                "pass",
                f"1:{armed_rr:.2f} passed the minimum",
            )
            decision["direction"] = armed.pattern.side
            decision["pattern"] = armed.pattern.name
            decision["volume_ratio"] = armed.pattern.volume_ratio
            decision["reward_risk"] = armed_rr
            if latest.timestamp_ms > armed.expires_at_ms:
                self.store.event("SIGNAL_EXPIRED", symbol, pattern=armed.pattern.name)
                self.store.update_signal_journal(
                    self.journal_ids.pop(symbol, None), symbol, "EXPIRED", reason="Trigger window expired"
                )
                decision["summary"] = "Armed setup expired before the trigger break"
                _set_check(decision, "trigger", "fail", "Trigger did not break within four 5m candles")
                self.store.decision_snapshot(symbol, decision)
                self.armed.pop(symbol, None)
                save_market("SCAN")
                self.notifier.send(format_alert(
                    "⌛ SIGNAL EXPIRED",
                    symbol=symbol,
                    status="NO ENTRY",
                    facts=[("🕯 Pattern", armed.pattern.name.replace("_", " "))],
                    action="Trigger window ended; setup removed",
                ))
                return
            invalid = (
                armed.pattern.side == "Buy" and latest.close <= armed.pattern.stop
            ) or (
                armed.pattern.side == "Sell" and latest.close >= armed.pattern.stop
            )
            if invalid:
                self.store.event("SIGNAL_INVALIDATED", symbol, pattern=armed.pattern.name)
                self.store.update_signal_journal(
                    self.journal_ids.pop(symbol, None), symbol, "INVALIDATED", reason="Price closed beyond stop"
                )
                decision["summary"] = "Armed setup was invalidated by price"
                _set_check(decision, "trigger", "fail", "Price closed beyond the pattern stop")
                self.store.decision_snapshot(symbol, decision)
                self.armed.pop(symbol, None)
                save_market("SCAN")
                self.notifier.send(format_alert(
                    "❌ SIGNAL INVALIDATED",
                    symbol=symbol,
                    status="NO ENTRY",
                    facts=[
                        ("🕯 Pattern", armed.pattern.name.replace("_", " ")),
                        ("🛡 Invalid beyond", f"{armed.pattern.stop:.8g}"),
                    ],
                    action="Setup removed; waiting for a new 5m confirmation",
                ))
                return
            is_later_candle = latest.timestamp_ms > armed.pattern.timestamp_ms
            triggered = (
                armed.pattern.side == "Buy" and latest.high >= armed.pattern.trigger
            ) or (
                armed.pattern.side == "Sell" and latest.low <= armed.pattern.trigger
            )
            if is_later_candle and triggered:
                entry = last_price or latest.close
                live_rr = reward_risk(entry, armed.pattern.stop, armed.target, armed.pattern.side)
                _set_check(decision, "trigger", "pass", f"Price broke {armed.pattern.trigger:.8g}")
                if live_rr < self.settings.min_reward_risk:
                    self.store.event("SIGNAL_SKIPPED_RR", symbol, entry=entry, target=armed.target)
                    self.store.update_signal_journal(
                        self.journal_ids.pop(symbol, None), symbol, "SKIPPED_RR",
                        entry=entry, reward_risk=live_rr, reason="Live reward-to-risk fell below minimum",
                    )
                    decision["summary"] = "Trigger broke, but live reward-to-risk became too small"
                    _set_check(
                        decision,
                        "rr",
                        "fail",
                        f"Live 1:{live_rr:.2f} is below 1:{self.settings.min_reward_risk:.2f}",
                    )
                else:
                    decision["summary"] = (
                        "Entry signal confirmed" if not self.settings.enable_order_placement
                        else "Entry confirmed — order execution requested"
                    )
                    self._enter(symbol, armed, entry)
                    self.store.update_signal_journal(
                        self.journal_ids.pop(symbol, None), symbol,
                        "ENTRY_CONFIRMED" if self.settings.enable_order_placement else "SIGNAL_ONLY",
                        entry=entry, reward_risk=live_rr,
                        reason="Trigger confirmed; signal-only mode" if not self.settings.enable_order_placement else "Order execution requested",
                    )
                self.store.decision_snapshot(symbol, decision)
                self.armed.pop(symbol, None)
                save_market("SCAN")
                return
            decision["summary"] = f"Setup armed — waiting for {armed.pattern.side.lower()} trigger break"
            _set_check(
                decision,
                "trigger",
                "wait",
                f"Waiting for price to break {armed.pattern.trigger:.8g}",
            )
            self.store.decision_snapshot(symbol, decision)
            return

        pattern = detect_pattern(candles_5m, context, self.settings.volume_multiplier)
        if pattern is None:
            self.store.decision_snapshot(symbol, decision)
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
            self.store.create_signal_journal(
                symbol, "REJECTED_RR", pattern=pattern.name, side=pattern.side,
                trigger=pattern.trigger, stop=pattern.stop, target=target,
                volume_ratio=pattern.volume_ratio, reward_risk=reward_risk(pattern.trigger, pattern.stop, target, pattern.side),
                reason="Pattern reward-to-risk below minimum",
            )
            decision["summary"] = "Pattern rejected because reward-to-risk is below the minimum"
            self.store.decision_snapshot(symbol, decision)
            return
        armed = ArmedSignal(
            pattern=pattern,
            armed_at_ms=latest.timestamp_ms,
            expires_at_ms=latest.timestamp_ms + 4 * FIVE_MINUTES_MS,
            target=target,
        )
        self.armed[symbol] = armed
        self.journal_ids[symbol] = self.store.create_signal_journal(
            symbol, "ARMED", pattern=pattern.name, side=pattern.side,
            trigger=pattern.trigger, stop=pattern.stop, target=target,
            volume_ratio=pattern.volume_ratio,
            reward_risk=reward_risk(pattern.trigger, pattern.stop, target, pattern.side),
            reason="Setup armed; waiting for trigger",
        )
        save_market("ARMED")
        decision["summary"] = f"Setup armed — waiting for {pattern.side.lower()} trigger break"
        _set_check(
            decision,
            "trigger",
            "wait",
            f"Waiting for price to break {pattern.trigger:.8g}",
        )
        self.store.decision_snapshot(symbol, decision)
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
            _signal_message(symbol, pattern.side, pattern.name, pattern.trigger, pattern.stop, target, pattern.volume_ratio)
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
        if self.settings.enable_order_placement:
            daily_loss = self._check_daily_loss_limit(force=True)
            if not daily_loss.get("available") or daily_loss.get("breached"):
                self.store.event(
                    "ENTRY_BLOCKED_DAILY_LOSS" if daily_loss.get("breached") else "ENTRY_BLOCKED_RISK_DATA",
                    symbol,
                    **daily_loss,
                )
                return
            # Re-check the exchange immediately before placing an order so a
            # manually opened position cannot be duplicated between scans.
            self._reconcile_exchange_positions(force=True)
        if symbol in self.blocked_symbols:
            self.store.event("ENTRY_BLOCKED_RECONCILIATION", symbol)
            self.notifier.send(format_alert(
                "⛔ ENTRY BLOCKED",
                symbol=symbol,
                status="EXCHANGE RECONCILIATION",
                action="Resolve the Bybit/local position mismatch before retrying",
            ))
            return
        if len(self.trades) >= self.settings.max_open_positions:
            self.store.event("ENTRY_BLOCKED_MAX_POSITIONS", symbol)
            self.notifier.send(format_alert(
                "⛔ ENTRY BLOCKED",
                symbol=symbol,
                status="MAX POSITIONS REACHED",
                facts=[("📦 Open", f"{len(self.trades)} / {self.settings.max_open_positions}")],
                action="Existing trades remain managed",
            ))
            return
        projected_risk = (len(self.trades) + 1) * self.settings.risk_per_trade
        if projected_risk > self.settings.max_total_open_risk + 1e-12:
            self.store.event("ENTRY_BLOCKED_TOTAL_RISK", symbol)
            self.notifier.send(format_alert(
                "⛔ ENTRY BLOCKED",
                symbol=symbol,
                status="TOTAL RISK LIMIT",
                facts=[("⚖️ Projected risk", f"{projected_risk * 100:.2f}%")],
                action="No order was sent",
            ))
            return
        self._record_v2_management_shadow(symbol, armed, expected_entry)
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
                "🧪 DEMO SIGNAL ONLY\n"
                "━━━━━━━━━━━━━━━━\n"
                f"📌 Pair: {symbol}\n"
                f"📊 Direction: {_side_label(armed.pattern.side)}\n"
                f"🕯 Pattern: {armed.pattern.name.replace('_', ' ')}\n"
                f"💵 Entry estimate: {expected_entry:.8g}\n"
                f"🛡 Stop loss: {armed.pattern.stop:.8g}\n"
                f"🏁 Target zone: {armed.target:.8g}\n"
                "ℹ️ Entry gate passed; no exchange order was sent"
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
        side_code = "b" if armed.pattern.side == "Buy" else "s"
        link_id = f"pa-{symbol[:8]}-{armed.pattern.timestamp_ms}-{side_code}"[:36]
        self.store.event("ORDER_SUBMIT_REQUESTED", symbol, order_link_id=link_id, quantity=qty)
        order_id = self.gateway.place_market_order(
            symbol,
            armed.pattern.side,
            qty,
            link_id,
            attempts=self.settings.order_retry_attempts,
        )
        self.store.event("ORDER_ACCEPTED", symbol, order_link_id=link_id, order_id=order_id)
        try:
            position = self.gateway.wait_for_position(symbol)
            actual_entry = float(position["avgPrice"])
            actual_qty = float(position["size"])
            # Keep only the exchange-side stop; staged targets are managed below.
            self.gateway.set_protection(symbol, armed.pattern.stop)
        except Exception:
            LOGGER.exception("Protection failed after entry; emergency-closing %s", symbol)
            self.gateway.close_partial(symbol, armed.pattern.side, qty)
            self.store.event("EMERGENCY_CLOSE_UNPROTECTED", symbol, order_id=order_id)
            raise

        tp1 = one_r_price(actual_entry, armed.pattern.stop, armed.pattern.side)
        risk = abs(actual_entry - tp1)
        tp2 = actual_entry + (1.5 * risk if armed.pattern.side == "Buy" else -1.5 * risk)
        trade = Trade(
            symbol=symbol,
            side=armed.pattern.side,
            qty=actual_qty,
            entry=actual_entry,
            stop=armed.pattern.stop,
            target=armed.target,
            one_r_target=tp1,
            order_id=order_id,
            tp2_target=tp2,
        )
        self.trades[symbol] = trade
        self.store.save_trade(trade)
        self.store.event("POSITION_OPENED", symbol, **{k: str(v) for k, v in trade.__dict__.items()})
        LOGGER.warning("Opened %s %s qty=%s entry=%s", trade.side, symbol, trade.qty, trade.entry)
        self.notifier.send(
            "✅ POSITION OPENED · DEMO · V3\n"
            "━━━━━━━━━━━━━━━━\n"
            f"📌 Pair: {symbol}\n"
            f"📊 Direction: {_side_label(trade.side)}\n"
            f"💵 Entry: {trade.entry:.8g}\n"
            f"📦 Quantity: {trade.qty:.8g}\n"
            f"⚙️ Leverage: {self.settings.leverage}× · risk {self.settings.risk_per_trade * 100:.2f}%\n"
            f"🛡 SL: {trade.stop:.8g}\n"
            f"🎯 TP1: {trade.one_r_target:.8g} · 50%\n"
            f"🎯 TP2: {trade.tp2_target:.8g} · 30%\n"
            f"🎯 TP3: {trade.target:.8g} · 20% runner\n"
            f"🧾 Entry order: {trade.order_id}\n"
            "🔁 Reversal protection: ACTIVE · Trade Audit: recorded"
        )

    def _record_v2_management_shadow(
        self, symbol: str, armed: ArmedSignal, expected_entry: float
    ) -> None:
        """Audit the locked recovery policy without changing V1 orders or management."""
        if self.settings.v2_management_mode != "shadow":
            return
        try:
            equity = self.gateway.equity_usdt()
            rules = self.gateway.instrument_rules(symbol)
            margin_qty_cap = equity * self.settings.leverage * 0.90 / expected_entry
            plan = build_recovery_plan(
                equity=equity,
                risk_fraction=self.settings.risk_per_trade,
                entry=expected_entry,
                stop=armed.pattern.stop,
                obstacle=armed.target,
                side=armed.pattern.side,
                qty_step=rules.qty_step,
                min_qty=rules.min_qty,
                max_qty=min(rules.max_market_qty, margin_qty_cap),
                policy=self.settings.recovery_policy(),
            )
            self.store.event(
                "V2_MANAGEMENT_SHADOW_ACCEPTED"
                if plan.accepted
                else "V2_MANAGEMENT_SHADOW_REJECTED",
                symbol,
                **plan.as_dict(),
            )
        except Exception as exc:
            LOGGER.exception("V2 management shadow failed for %s", symbol)
            self.store.event(
                "V2_MANAGEMENT_SHADOW_ERROR",
                symbol,
                error_type=type(exc).__name__,
            )

    def _manage_open_trades(self, prices: dict[str, float]) -> None:
        if not self.settings.enable_order_placement:
            return
        for symbol, trade in list(self.trades.items()):
            try:
                position = self.gateway.position(symbol)
                if not position:
                    close_event = (
                        "TRAILING_STOP_CLOSED"
                        if trade.state == SignalState.TRAILING
                        else "STOP_LOSS_CLOSED"
                    )
                    self.store.event(
                        close_event,
                        symbol,
                        side=trade.side,
                        entry=trade.entry,
                        protective_stop=trade.stop,
                    )
                    self.store.close_trade(
                        symbol,
                        close_event,
                        final_stop=trade.stop,
                        management_state=trade.state.value,
                    )
                    self.trades.pop(symbol, None)
                    close_label = "TRAILING STOP" if close_event == "TRAILING_STOP_CLOSED" else "STOP LOSS"
                    self.notifier.send(
                        f"🛑 {close_label} CLOSED · DEMO\n"
                        "━━━━━━━━━━━━━━━━\n"
                        f"📌 Pair: {symbol}\n"
                        f"📊 Position: {_side_label(trade.side)}\n"
                        f"💵 Entry: {trade.entry:.8g}\n"
                        f"🛡 Final stop: {trade.stop:.8g}\n"
                        "✅ Position is no longer open\n"
                        "📊 Bybit-confirmed result will follow once available"
                    )
                    continue
                price = prices.get(symbol) or float(position["markPrice"])
                if trade.tp2_target <= 0:
                    risk = abs(trade.entry - trade.one_r_target)
                    trade.tp2_target = trade.entry + (
                        1.5 * risk if trade.side == "Buy" else -1.5 * risk
                    )
                    self.store.save_trade(trade)
                management_candles = self.gateway.candles(symbol, "5", 40)
                reversal = detect_trade_reversal(
                    management_candles, trade.side, self.settings.volume_multiplier
                )
                if reversal:
                    close_qty = float(position.get("size") or trade.qty)
                    self.gateway.close_partial(symbol, trade.side, close_qty)
                    self.store.event(
                        "EARLY_EXIT_REVERSAL",
                        symbol,
                        side=trade.side,
                        pattern=reversal["pattern"],
                        volume_ratio=reversal["volume_ratio"],
                        reason=reversal["reason"],
                    )
                    self.store.close_trade(
                        symbol,
                        "REVERSAL_EXIT",
                        pattern=reversal["pattern"],
                        price=price,
                    )
                    self.trades.pop(symbol, None)
                    self.notifier.send(
                        "⚠️ EARLY EXIT · REVERSAL\n"
                        "━━━━━━━━━━━━━━━━\n"
                        f"📌 Pair: {symbol}\n"
                        f"📊 Position: {_side_label(trade.side)}\n"
                        f"🕯 Opposite pattern: {reversal['pattern'].replace('_', ' ')}\n"
                        f"📈 Volume: {float(reversal['volume_ratio']):.2f}× average\n"
                        f"📝 Reason: {reversal['reason']}\n"
                        "🛑 Remaining position closed for protection\n"
                        "📊 Bybit-confirmed result will follow once available"
                    )
                    continue
                reached_one_r = (
                    trade.side == "Buy" and price >= trade.one_r_target
                ) or (
                    trade.side == "Sell" and price <= trade.one_r_target
                )
                if reached_one_r and not trade.partial_taken:
                    self.gateway.close_partial(symbol, trade.side, trade.qty * 0.50)
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
                        "💰 TP1 HIT · 50% CLOSED\n"
                        f"📌 Pair: {symbol}\n"
                        f"📦 Closed quantity: {trade.qty * 0.50:.8g}\n"
                        f"💵 Fill: {price:.8g}\n"
                        f"🛡 New stop: {new_stop:.8g}\n"
                        "🔒 Risk reduced; runner remains active"
                    )

                reached_tp2 = (
                    trade.side == "Buy" and price >= trade.tp2_target
                ) or (
                    trade.side == "Sell" and price <= trade.tp2_target
                )
                if trade.partial_taken and reached_tp2 and not trade.tp2_taken:
                    self.gateway.close_partial(symbol, trade.side, trade.qty * 0.30)
                    trade.tp2_taken = True
                    trade.state = SignalState.PARTIAL_TP
                    self.store.save_trade(trade)
                    self.store.event("PARTIAL_TP2", symbol, price=price, qty_fraction=0.30)
                    self.notifier.send(
                        "💰 TP2 HIT · 30% CLOSED\n"
                        f"📌 Pair: {symbol}\n"
                        f"📦 Closed quantity: {trade.qty * 0.30:.8g}\n"
                        f"💵 Fill: {price:.8g}\n"
                        "📌 20% runner remains for TP3"
                    )

                reached_tp3 = (
                    trade.side == "Buy" and price >= trade.target
                ) or (
                    trade.side == "Sell" and price <= trade.target
                )
                if trade.tp2_taken and reached_tp3 and not trade.tp3_taken:
                    remaining_qty = float(position.get("size") or trade.qty * 0.20)
                    self.gateway.close_partial(symbol, trade.side, remaining_qty)
                    trade.tp3_taken = True
                    self.store.save_trade(trade)
                    self.store.close_trade(
                        symbol,
                        "TP3_COMPLETE",
                        price=price,
                        quantity=remaining_qty,
                    )
                    self.trades.pop(symbol, None)
                    self.store.event("TP3_CLOSED", symbol, price=price, qty=remaining_qty)
                    self.notifier.send(
                        "🏁 TP3 HIT · TRADE COMPLETE\n"
                        f"📌 Pair: {symbol}\n"
                        f"📦 Closed quantity: {remaining_qty:.8g}\n"
                        f"💵 Final fill: {price:.8g}\n"
                        "✅ 100% planned position exited\n"
                        "📊 Bybit-confirmed result will follow once available"
                    )
                    continue

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
                            self.notifier.send(format_alert(
                                "🔒 TRAILING STOP UPDATED",
                                symbol=symbol,
                                status="POSITION PROTECTED",
                                facts=[
                                    ("📊 Direction", _side_label(trade.side)),
                                    ("🛡 New stop", f"{candidate:.8g}"),
                                    ("💵 Market", f"{price:.8g}"),
                                ],
                                action="Remaining runner stays open",
                            ))
            except Exception:
                LOGGER.exception("Trade management failed for %s", symbol)
                self.store.event("TRADE_MANAGEMENT_ERROR", symbol)

    def _reconcile_exchange_positions(self, force: bool = False) -> None:
        """Align local state with Bybit without adopting unknown positions."""
        if not self.settings.enable_order_placement:
            self.reconciliation = {
                "status": "disabled",
                "last_checked_at_ms": 0,
                "exchange_open": 0,
                "tracked_open": len(self.trades),
                "blocked_symbols": [],
                "issues": [],
            }
            return
        now_ms = int(time.time() * 1000)
        if not force and now_ms - self.last_reconcile_ms < RECONCILE_INTERVAL_MS:
            return
        exchange_rows = self.gateway.open_positions()
        exchange_positions = {
            str(row.get("symbol") or ""): row
            for row in exchange_rows
            if row.get("symbol")
        }
        previous_blocked = set(self.blocked_symbols)
        issues: list[dict[str, object]] = []

        for symbol, trade in list(self.trades.items()):
            position = exchange_positions.get(symbol)
            if not position:
                self.store.close_trade(
                    symbol,
                    "EXCHANGE_RECONCILIATION",
                    management_state=trade.state.value,
                )
                self.trades.pop(symbol, None)
                self.store.event(
                    "RECONCILED_LOCAL_CLOSED",
                    symbol,
                    side=trade.side,
                    reason="Local trade was not open on Bybit during reconciliation",
                )
                self.notifier.send(
                    "🔄 POSITION RECONCILED · DEMO\n"
                    "━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair: {symbol}\n"
                    "ℹ️ Bybit reports no open position; local position was closed\n"
                    "📊 Final result is available in Real P&L"
                )

        healthy_matches: set[str] = set()
        for symbol, position in exchange_positions.items():
            trade = self.trades.get(symbol)
            if not trade:
                issues.append({"symbol": symbol, "type": "untracked_exchange_position"})
                if symbol not in previous_blocked:
                    self.store.event(
                        "UNTRACKED_EXCHANGE_POSITION",
                        symbol,
                        side=position.get("side"),
                        qty=position.get("size"),
                    )
                    self.notifier.send(
                        "🚨 RECONCILIATION ALERT · DEMO\n"
                        "━━━━━━━━━━━━━━━━\n"
                        f"📌 Pair: {symbol}\n"
                        f"📊 Bybit position: {position.get('side')} · qty {position.get('size')}\n"
                        "⛔ No matching local trade; new bot entry is blocked\n"
                        "👤 Review this position manually"
                    )
                continue

            exchange_side = str(position.get("side") or "")
            if exchange_side != trade.side:
                issues.append({"symbol": symbol, "type": "side_mismatch"})
                if symbol not in previous_blocked:
                    self.store.event(
                        "POSITION_SIDE_MISMATCH",
                        symbol,
                        local_side=trade.side,
                        exchange_side=exchange_side,
                    )
                    self.notifier.send(
                        "🚨 POSITION SIDE MISMATCH · DEMO\n"
                        "━━━━━━━━━━━━━━━━\n"
                        f"📌 Pair: {symbol}\n"
                        f"💾 Local: {trade.side} · Bybit: {exchange_side}\n"
                        "⛔ Automated entry is blocked; manual review required"
                    )
                continue

            healthy_matches.add(symbol)
            changed = False
            exchange_qty = float(position.get("size") or 0)
            if not trade.partial_taken and abs(exchange_qty - trade.qty) > max(1e-12, exchange_qty * 1e-6):
                old_qty = trade.qty
                trade.qty = exchange_qty
                changed = True
                self.store.event(
                    "POSITION_QTY_RECONCILED",
                    symbol,
                    local_qty=old_qty,
                    exchange_qty=exchange_qty,
                )
            exchange_stop = float(position.get("stopLoss") or 0)
            if exchange_stop > 0 and abs(exchange_stop - trade.stop) > max(1e-12, exchange_stop * 1e-8):
                old_stop = trade.stop
                trade.stop = exchange_stop
                changed = True
                self.store.event(
                    "POSITION_STOP_RECONCILED",
                    symbol,
                    local_stop=old_stop,
                    exchange_stop=exchange_stop,
                )
            if changed:
                self.store.save_trade(trade)

        current_blocked = {str(issue["symbol"]) for issue in issues}
        for symbol in previous_blocked - current_blocked:
            self.store.event("RECONCILIATION_CLEARED", symbol)
        self.blocked_symbols = current_blocked
        self.symbols = list(dict.fromkeys(self.symbols + list(exchange_positions)))
        self.last_reconcile_ms = now_ms
        self.reconciliation = {
            "status": "attention" if issues else "healthy",
            "last_checked_at_ms": now_ms,
            "exchange_open": len(exchange_positions),
            "tracked_open": len(healthy_matches),
            "blocked_symbols": sorted(self.blocked_symbols),
            "issues": issues,
        }

    def _notify_new_closed_pnl(self, initialize: bool = False) -> None:
        """Send one exchange-confirmed result per new Bybit closed-PnL row."""
        if not self.settings.enable_order_placement:
            return
        now_ms = int(time.time() * 1000)
        if not initialize and now_ms - self.last_pnl_poll_ms < 15_000:
            return
        dhaka = timezone(timedelta(hours=6))
        local_now = datetime.now(dhaka)
        start = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=dhaka)
        try:
            rows = self.gateway.closed_pnl(int(start.timestamp() * 1000), limit=200)
        except Exception:
            LOGGER.exception("Exchange P&L notification poll failed")
            self.store.event("EXCHANGE_PNL_POLL_ERROR")
            return
        self.last_pnl_poll_ms = now_ms
        identities = {
            str(row.get("orderId") or f"{row.get('symbol')}:{row.get('updatedTime')}:{row.get('closedPnl')}")
            for row in rows
        }
        if initialize:
            self.notified_closed_pnl_ids.update(identities)
            return
        for row in sorted(rows, key=lambda item: int(item.get("updatedTime") or 0)):
            identity = str(
                row.get("orderId")
                or f"{row.get('symbol')}:{row.get('updatedTime')}:{row.get('closedPnl')}"
            )
            if identity in self.notified_closed_pnl_ids:
                continue
            pnl = float(row.get("closedPnl") or 0)
            fees = abs(float(row.get("openFee") or 0)) + abs(float(row.get("closeFee") or 0))
            self.notified_closed_pnl_ids.add(identity)
            self.store.event(
                "EXCHANGE_PNL_CONFIRMED",
                str(row.get("symbol") or ""),
                order_id=identity,
                closed_pnl=pnl,
                fees=fees,
                avg_entry=row.get("avgEntryPrice"),
                avg_exit=row.get("avgExitPrice"),
                qty=row.get("qty"),
            )
            self.notifier.send(format_alert(
                "✅ BYBIT EXIT CONFIRMED" if pnl >= 0 else "📉 BYBIT EXIT CONFIRMED",
                symbol=str(row.get("symbol") or ""),
                status="PROFIT" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN",
                facts=[
                    ("📊 Closed side", row.get("side") or "—"),
                    ("📦 Quantity", row.get("qty") or "—"),
                    ("💵 Avg entry", row.get("avgEntryPrice") or "—"),
                    ("🏁 Avg exit", row.get("avgExitPrice") or "—"),
                    ("💰 Net P&L", f"{pnl:+.6f} USDT"),
                    ("🧾 Trading fees", f"{fees:.6f} USDT"),
                ],
                action="Recorded in Trade Audit",
            ))

    def run_forever(self) -> None:
        def request_stop(signum: int, _frame: object) -> None:
            LOGGER.warning("Received signal %s; stopping after current cycle", signum)
            self._stop_event.set()

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, request_stop)
            signal.signal(signal.SIGINT, request_stop)
        self.refresh_universe(force=True)
        self._reconcile_exchange_positions(force=True)
        self._notify_new_closed_pnl(initialize=True)
        mode = "DEMO ORDERS" if self.settings.enable_order_placement else "SIGNAL ONLY"
        LOGGER.warning("Bot started in %s mode", mode)
        self.notifier.send(
            "🚀 PRICE ACTION BOT STARTED · ALERTS V3\n"
            "━━━━━━━━━━━━━━━━\n"
            f"🧪 Mode: {mode}\n"
            f"⚙️ Leverage: {self.settings.leverage}×\n"
            f"📌 Universe: {len(self.symbols)} symbols\n"
            "✅ Lifecycle + exchange-confirmed P&L alerts are active"
        )
        started_at_ms = int(time.time() * 1000)
        self.store.heartbeat(
            "running",
            mode=mode,
            symbols=self.symbols,
            entries_enabled=self.store.trading_enabled(),
            scanner_status="starting",
            last_scan_at_ms=0,
            next_scan_at_ms=started_at_ms,
            reconciliation=self.reconciliation,
            daily_loss=self.daily_loss,
        )
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.run_once()
                finished_at_ms = int(time.time() * 1000)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                next_scan_at_ms = finished_at_ms + max(
                    1_000, int((self.settings.poll_seconds - (elapsed_ms / 1000)) * 1000)
                )
                self.store.heartbeat(
                    "running",
                    mode=mode,
                    symbols=self.symbols,
                    entries_enabled=self.store.trading_enabled(),
                    open_positions=len(self.trades),
                    scanner_status="healthy",
                    scanned_symbols=len(self.symbols),
                    last_scan_at_ms=finished_at_ms,
                    last_scan_duration_ms=elapsed_ms,
                    next_scan_at_ms=next_scan_at_ms,
                    reconciliation=self.reconciliation,
                    daily_loss=self.daily_loss,
                )
            except Exception:
                LOGGER.exception("Engine cycle failed")
                self.store.event("ENGINE_CYCLE_ERROR")
                failed_at_ms = int(time.time() * 1000)
                self.store.heartbeat(
                    "error",
                    mode=mode,
                    symbols=self.symbols,
                    scanner_status="error",
                    last_scan_at_ms=failed_at_ms,
                    next_scan_at_ms=failed_at_ms + self.settings.poll_seconds * 1000,
                    reconciliation=self.reconciliation,
                    daily_loss=self.daily_loss,
                )
            elapsed = time.monotonic() - started
            self._stop_event.wait(max(1.0, self.settings.poll_seconds - elapsed))
        self.store.heartbeat("stopped", mode=mode)
        LOGGER.warning("Bot stopped cleanly")

    def stop(self) -> None:
        self._stop_event.set()
