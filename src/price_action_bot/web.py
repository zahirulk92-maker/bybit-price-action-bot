from __future__ import annotations

import secrets
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from .config import Settings
from .engine import TradingEngine
from .exchange import BybitGateway
from .notify import TelegramNotifier
from .store import Store


settings = Settings.from_env()
store = Store(settings.database_path, settings.database_url)
chart_gateway = BybitGateway(settings.api_key, settings.api_secret, settings.demo)
security = HTTPBasic(auto_error=False)
static_dir = Path(__file__).with_name("static")
LOGGER = logging.getLogger(__name__)
engine_host_stop = threading.Event()
engine_ref: dict[str, TradingEngine] = {}
chart_cache_lock = threading.Lock()
chart_cache: dict[tuple[str, str], tuple[float, dict[str, object]]] = {}
wallet_cache_lock = threading.Lock()
wallet_cache: tuple[float, dict[str, object]] | None = None
pnl_cache_lock = threading.Lock()
pnl_cache: tuple[float, dict[str, object]] | None = None
audit_cache_lock = threading.Lock()
audit_pnl_cache: dict[int, tuple[float, dict[str, object]]] = {}


def _wallet_status() -> dict[str, object]:
    global wallet_cache
    if not settings.api_key or not settings.api_secret:
        return {"available": False, "error": "Demo API credentials are not configured"}
    now = time.monotonic()
    with wallet_cache_lock:
        if wallet_cache and wallet_cache[0] > now:
            return wallet_cache[1]
    try:
        payload: dict[str, object] = {"available": True, **chart_gateway.wallet_summary()}
    except Exception as exc:
        LOGGER.warning("Wallet summary unavailable: %s", exc)
        payload = {"available": False, "error": "Bybit wallet temporarily unavailable"}
    with wallet_cache_lock:
        wallet_cache = (now + 15, payload)
    return payload


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _pnl_status_since(start_ms: int, limit: int = 500) -> dict[str, object]:
    """Summarize authoritative Bybit closed-PnL rows since a timestamp."""
    try:
        raw_rows = chart_gateway.closed_pnl(start_ms, limit=limit)
        rows = [
            {
                "symbol": str(row.get("symbol") or ""),
                "side": str(row.get("side") or ""),
                "qty": _number(row.get("qty")),
                "entry": _number(row.get("avgEntryPrice")),
                "exit": _number(row.get("avgExitPrice")),
                "closed_pnl": _number(row.get("closedPnl")),
                "fees": abs(_number(row.get("openFee"))) + abs(_number(row.get("closeFee"))),
                "updated_at_ms": int(_number(row.get("updatedTime") or row.get("createdTime"))),
                "order_id": str(row.get("orderId") or ""),
            }
            for row in raw_rows
        ]
        rows.sort(key=lambda row: int(row["updated_at_ms"]), reverse=True)
        wins = sum(_number(row["closed_pnl"]) > 0 for row in rows)
        losses = sum(_number(row["closed_pnl"]) < 0 for row in rows)
        decided = wins + losses
        payload: dict[str, object] = {
            "available": True,
            "source": "Bybit closed P&L",
            "realized_pnl": sum(_number(row["closed_pnl"]) for row in rows),
            "fees": sum(_number(row["fees"]) for row in rows),
            "closed_count": len(rows),
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / decided * 100) if decided else 0.0,
            "since_ms": start_ms,
            "rows": rows,
        }
    except Exception as exc:
        LOGGER.warning("Real P&L unavailable: %s", exc)
        payload = {"available": False, "error": "Bybit closed P&L temporarily unavailable", "rows": []}
    return payload


def _real_pnl_status() -> dict[str, object]:
    """Summarize today's authoritative Bybit closed-PnL rows."""
    global pnl_cache
    if not settings.api_key or not settings.api_secret:
        return {"available": False, "error": "Demo API credentials are not configured", "rows": []}
    now = time.monotonic()
    with pnl_cache_lock:
        if pnl_cache and pnl_cache[0] > now:
            return pnl_cache[1]
    dhaka = timezone(timedelta(hours=6))
    local_now = datetime.now(dhaka)
    start = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=dhaka)
    payload = _pnl_status_since(int(start.timestamp() * 1000), limit=200)
    payload["rows"] = list(payload.get("rows") or [])[:30]
    with pnl_cache_lock:
        pnl_cache = (now + 15, payload)
    return payload


def _today_summary(open_trades: list[dict[str, object]]) -> dict[str, int]:
    dhaka = timezone(timedelta(hours=6))
    now = datetime.now(dhaka)
    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=dhaka)
    events = store.events_since(int(start.timestamp() * 1000), limit=2000)
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event["event_type"])
        counts[event_type] = counts.get(event_type, 0) + 1
    open_rows = [row for row in open_trades if row.get("status") == "open"]
    trailing = sum(
        str((row.get("trade") or {}).get("state", "")) == "TRAILING" for row in open_rows
    )
    tp_closed = counts.get("TP3_CLOSED", 0)
    sl_closed = counts.get("STOP_LOSS_CLOSED", 0)
    trailing_closed = counts.get("TRAILING_STOP_CLOSED", 0)
    reversal_closed = counts.get("EARLY_EXIT_REVERSAL", 0)
    other_closed = counts.get("POSITION_CLOSED", 0)
    return {
        "opened": counts.get("POSITION_OPENED", 0),
        "closed": tp_closed + sl_closed + trailing_closed + reversal_closed + other_closed,
        "remaining": len(open_rows),
        "tp_closed": tp_closed,
        "sl_closed": sl_closed,
        "trailing_closed": trailing_closed,
        "reversal_closed": reversal_closed,
        "other_closed": other_closed,
        "tp1_hits": counts.get("PARTIAL_TP", 0),
        "tp2_hits": counts.get("PARTIAL_TP2", 0),
        "trailing_active": trailing,
    }


def _run_embedded_engine() -> None:
    while not engine_host_stop.is_set():
        if not store.try_acquire_engine_lock():
            store.heartbeat("standby", reason="another instance owns the engine lock")
            engine_host_stop.wait(5)
            continue
        try:
            engine = TradingEngine(
                settings,
                BybitGateway(settings.api_key, settings.api_secret, settings.demo),
                store,
                TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id),
            )
            engine_ref["engine"] = engine
            engine.run_forever()
        except Exception:
            LOGGER.exception("Embedded trading engine stopped unexpectedly")
            store.heartbeat("error", reason="embedded engine stopped")
        finally:
            engine_ref.pop("engine", None)
            store.release_engine_lock()
        if not engine_host_stop.is_set():
            engine_host_stop.wait(5)


@asynccontextmanager
async def lifespan(_: FastAPI):
    thread: threading.Thread | None = None
    if settings.run_engine_in_web:
        engine_host_stop.clear()
        thread = threading.Thread(target=_run_embedded_engine, name="trading-engine", daemon=True)
        thread.start()
    try:
        yield
    finally:
        engine_host_stop.set()
        if engine := engine_ref.get("engine"):
            engine.stop()
        if thread:
            thread.join(timeout=55)

app = FastAPI(title="Price Action Control", docs_url=None, redoc_url=None, lifespan=lifespan)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    if settings.dashboard_allow_insecure_local:
        return "local-preview"
    if not settings.dashboard_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard credentials are not configured",
        )
    valid = bool(credentials) and secrets.compare_digest(
        credentials.username.encode(), settings.dashboard_username.encode()
    ) and secrets.compare_digest(
        credentials.password.encode(), settings.dashboard_password.encode()
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


class ControlRequest(BaseModel):
    enabled: bool


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=FileResponse)
def dashboard(_: str = Depends(require_auth)) -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/audit", response_class=FileResponse)
def audit_dashboard(_: str = Depends(require_auth)) -> FileResponse:
    return FileResponse(static_dir / "audit.html")


@app.get("/api/audit")
def api_audit(
    days: int = Query(7, ge=1, le=7),
    symbol: str = Query("", max_length=30),
    _: str = Depends(require_auth),
) -> dict[str, object]:
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - days * 24 * 60 * 60 * 1000
    wanted = symbol.strip().upper()
    now = time.monotonic()
    with audit_cache_lock:
        cached = audit_pnl_cache.get(days)
        if cached and cached[0] > now:
            pnl = cached[1]
        elif not settings.api_key or not settings.api_secret:
            pnl = {"available": False, "error": "Demo API credentials are not configured", "rows": []}
        else:
            pnl = _pnl_status_since(since_ms)
            audit_pnl_cache[days] = (now + 30, pnl)
    events = store.events_since(since_ms, limit=2000)
    signals = [
        row for row in store.recent_signal_journal(1000)
        if int(row.get("updated_at_ms") or 0) >= since_ms
    ]
    trades = [
        row for row in store.recent_trades(1000)
        if int(row.get("updated_at_ms") or 0) >= since_ms
    ]
    pnl_rows = list(pnl.get("rows") or [])
    if wanted:
        events = [row for row in events if row.get("symbol") == wanted]
        signals = [row for row in signals if row.get("symbol") == wanted]
        trades = [row for row in trades if row.get("symbol") == wanted]
        pnl_rows = [row for row in pnl_rows if row.get("symbol") == wanted]
    realized = sum(_number(row.get("closed_pnl")) for row in pnl_rows)
    fees = sum(_number(row.get("fees")) for row in pnl_rows)
    wins = sum(_number(row.get("closed_pnl")) > 0 for row in pnl_rows)
    losses = sum(_number(row.get("closed_pnl")) < 0 for row in pnl_rows)
    heartbeat = store.get_heartbeat()
    reconciliation = (heartbeat.get("details") or {}).get("reconciliation", {})
    return {
        "generated_at_ms": now_ms,
        "since_ms": since_ms,
        "days": days,
        "symbol": wanted,
        "summary": {
            "realized_pnl": realized,
            "fees": fees,
            "exchange_exits": len(pnl_rows),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / (wins + losses) * 100 if wins + losses else 0.0,
            "local_trades": len(trades),
            "events": len(events),
            "signals": len(signals),
        },
        "pnl_available": bool(pnl.get("available")),
        "pnl_error": pnl.get("error", ""),
        "reconciliation": reconciliation,
        "exchange_exits": pnl_rows[:500],
        "trades": trades,
        "events": list(reversed(events)),
        "signals": signals,
    }


@app.get("/api/status")
def api_status(_: str = Depends(require_auth)) -> dict[str, object]:
    heartbeat = store.get_heartbeat()
    now_ms = int(time.time() * 1000)
    heartbeat_age = now_ms - int(heartbeat.get("updated_at_ms", 0)) if heartbeat else None
    heartbeat_details = heartbeat.get("details", {}) if heartbeat else {}
    last_scan_at_ms = int(heartbeat_details.get("last_scan_at_ms", 0) or 0)
    scanner_age = now_ms - last_scan_at_ms if last_scan_at_ms else None
    scanner_status = str(heartbeat_details.get("scanner_status", "starting"))
    scanner_stale_after_ms = max(90_000, settings.poll_seconds * 3_000)
    if scanner_status == "healthy" and scanner_age is not None and scanner_age > scanner_stale_after_ms:
        scanner_status = "stale"
    scanner = {
        "status": scanner_status,
        "last_scan_at_ms": last_scan_at_ms,
        "last_scan_age_ms": scanner_age,
        "next_scan_at_ms": int(heartbeat_details.get("next_scan_at_ms", 0) or 0),
        "last_scan_duration_ms": int(heartbeat_details.get("last_scan_duration_ms", 0) or 0),
        "scanned_symbols": int(heartbeat_details.get("scanned_symbols", 0) or 0),
        "stale_after_ms": scanner_stale_after_ms,
    }
    safety = {
        "environment": "DEMO LOCKED" if settings.demo else "LIVE ACKNOWLEDGED",
        "execution": "SIGNAL ONLY" if not settings.enable_order_placement else "ORDERS ENABLED",
        "entries": "ENABLED" if store.trading_enabled() else "PAUSED",
        "risk_guard": "ACTIVE",
        "live_lock": not settings.demo and settings.live_trading_ack == "I_UNDERSTAND_LIVE_RISK",
        "max_positions": settings.max_open_positions,
        "max_total_risk": settings.max_total_open_risk,
    }
    trades = store.recent_trades(100)
    return {
        "environment": "demo" if settings.demo else "live",
        "execution": "orders" if settings.enable_order_placement else "signals",
        "worker_online": heartbeat_age is not None and heartbeat_age < 90_000,
        "heartbeat": heartbeat,
        "scanner": scanner,
        "wallet": _wallet_status(),
        "pnl": _real_pnl_status(),
        "today": _today_summary(trades),
        "reconciliation": heartbeat_details.get(
            "reconciliation",
            {
                "status": "pending",
                "last_checked_at_ms": 0,
                "exchange_open": 0,
                "tracked_open": 0,
                "blocked_symbols": [],
                "issues": [],
            },
        ),
        "safety": safety,
        "trading_enabled": store.trading_enabled(),
        "markets": store.market_snapshots(),
        "decisions": store.decision_snapshots(),
        "signal_journal": store.recent_signal_journal(100),
        "open_trades": trades,
        "events": store.recent_events(40),
        "strategy": {
            "leverage": settings.leverage,
            "risk_per_trade": settings.risk_per_trade,
            "max_positions": settings.max_open_positions,
            "min_reward_risk": settings.min_reward_risk,
            "volume_multiplier": settings.volume_multiplier,
            "universe_size": settings.universe_size,
        },
    }


@app.get("/api/chart/{symbol}")
def api_chart(
    symbol: str,
    interval: str = "5",
    limit: int = 160,
    _: str = Depends(require_auth),
) -> dict[str, object]:
    symbol = symbol.upper()
    if interval not in {"5", "15", "60"}:
        raise HTTPException(status_code=400, detail="Supported intervals: 5, 15, 60")
    markets = {row["symbol"]: row for row in store.market_snapshots()}
    if symbol not in markets:
        raise HTTPException(status_code=404, detail="Symbol is not in the active universe")
    limit = max(60, min(limit, 240))
    key = (symbol, interval)
    now = time.monotonic()
    with chart_cache_lock:
        cached = chart_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    candles = chart_gateway.candles(symbol, interval, limit)
    market = markets[symbol]
    payload: dict[str, object] = {
        "symbol": symbol,
        "interval": interval,
        "bias": market["bias"],
        "support": market["support"],
        "resistance": market["resistance"],
        "candles": [
            {
                "time": candle.timestamp_ms,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles
        ],
    }
    with chart_cache_lock:
        chart_cache[key] = (now + 15, payload)
    return payload


@app.post("/api/control")
def api_control(request: ControlRequest, _: str = Depends(require_auth)) -> dict[str, bool]:
    store.set_trading_enabled(request.enabled)
    return {"enabled": store.trading_enabled()}
