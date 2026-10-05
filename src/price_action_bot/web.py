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
security = HTTPBasic(auto_error=False)
static_dir = Path(__file__).with_name("static")
LOGGER = logging.getLogger(__name__)
engine_host_stop = threading.Event()
engine_ref: dict[str, TradingEngine] = {}


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
    heartbeat_age = (
        int(time.time() * 1000) - int(heartbeat.get("updated_at_ms", 0)) if heartbeat else None
    )
    return {
        "environment": "demo" if settings.demo else "live",
        "execution": "orders" if settings.enable_order_placement else "signals",
        "worker_online": heartbeat_age is not None and heartbeat_age < 90_000,
        "heartbeat": heartbeat,
        "trading_enabled": store.trading_enabled(),
        "markets": store.market_snapshots(),
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


@app.post("/api/control")
def api_control(request: ControlRequest, _: str = Depends(require_auth)) -> dict[str, bool]:
    store.set_trading_enabled(request.enabled)
    return {"enabled": store.trading_enabled()}
