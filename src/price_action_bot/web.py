from __future__ import annotations

import secrets
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
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
    return {
        "environment": "demo" if settings.demo else "live",
        "execution": "orders" if settings.enable_order_placement else "signals",
        "worker_online": heartbeat_age is not None and heartbeat_age < 90_000,
        "heartbeat": heartbeat,
        "scanner": scanner,
        "trading_enabled": store.trading_enabled(),
        "markets": store.market_snapshots(),
        "decisions": store.decision_snapshots(),
        "open_trades": store.recent_trades(100),
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
